"""A small interpreter for the Pascal dialect of Cossacks 3 scripts (DMScript).

Enough of the language to run data/scripts/lib/country.script: nested
procedures and functions, var parameters, const/var/type declarations inside
blocks, if/case/for/while, records and arrays (auto-created on first use),
string and integer arithmetic. Engine functions it does not know return an
empty value and are counted in `Interpreter.unknown`, so a run shows what it
skipped.

The game files belong to GSC Game World; this reads them where the user
installed them and keeps only derived data.
"""

import re

TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>//[^\n]*|\{[^}]*\}|\(\*.*?\*\))
  | (?P<str>'(?:[^']|'')*')
  | (?P<char>\#\d+)
  | (?P<num>\$[0-9A-Fa-f]+|\d+\.\d+(?:[eE][-+]?\d+)?|\d+)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<sym>:=|<>|<=|>=|\.\.|[-+*/=<>()\[\],;:.^@])
""", re.S | re.X)

KEYWORDS = {"begin", "end", "if", "then", "else", "case", "of", "for", "to", "downto", "do", "while", "repeat",
            "until", "var", "const", "type", "procedure", "function", "and", "or", "not", "div", "mod", "shl", "shr",
            "xor", "class", "record", "array", "exit", "break", "continue", "in"}


def tokenize(src):
    out, pos = [], 0
    while pos < len(src):
        m = TOKEN_RE.match(src, pos)
        if not m:
            pos += 1  # a stray character (non-ASCII in a comment): skip
            continue
        pos = m.end()
        kind = m.lastgroup
        text = m.group()
        if kind in ("ws", "comment"):
            continue
        if kind == "str":
            out.append(("str", text[1:-1].replace("''", "'")))
        elif kind == "char":
            out.append(("str", chr(int(text[1:]))))
        elif kind == "num":
            out.append(("num", int(text[1:], 16) if text[0] == "$" else (float(text) if "." in text else int(text))))
        elif kind == "id":
            low = text.lower()
            out.append(("kw", low) if low in KEYWORDS else ("id", low))
        else:
            out.append(("sym", text))
    out.append(("eof", None))
    return out


# ---------------------------------------------------------------- values

class _Empty:
    """A value never assigned: 0, '' and False at once."""

    def __repr__(self):
        return "Empty"

    def __bool__(self):
        return False

    def __str__(self):
        return ""

    def __int__(self):
        return 0

    def __eq__(self, other):
        return other in (0, "", False) or other is self or isinstance(other, (_Empty, Box))

    def __hash__(self):
        return 0


EMPTY = _Empty()


class Box:
    """A record, object or array: fields and items appear on first use."""

    __slots__ = ("fields", "items")

    def __init__(self):
        self.fields, self.items = {}, {}

    def get_field(self, name):
        if name not in self.fields:
            self.fields[name] = Box()
        return self.fields[name]

    def get_item(self, key):
        if key not in self.items:
            self.items[key] = Box()
        return self.items[key]

    def __bool__(self):
        return False

    def __str__(self):
        return ""

    def __eq__(self, other):
        return other in (0, "", False) or isinstance(other, (_Empty, Box))

    def __hash__(self):
        return id(self)


def plain(v):
    """Box / EMPTY -> a scalar default for arithmetic."""
    return EMPTY if isinstance(v, Box) else v


def num(v):
    v = plain(v)
    if v is EMPTY or v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    return v


def text(v):
    v = plain(v)
    if v is EMPTY or v is None:
        return ""
    return v if isinstance(v, str) else str(v)


class Cell:
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class RefCell:
    """A var parameter: reads and writes go to the caller's location."""

    __slots__ = ("getter", "setter")

    def __init__(self, getter, setter):
        self.getter, self.setter = getter, setter

    def get(self):
        return self.getter()

    def set(self, value):
        self.setter(value)


class Env:
    __slots__ = ("vars", "parent")

    def __init__(self, parent=None):
        self.vars, self.parent = {}, parent

    def find(self, name):
        env = self
        while env is not None:
            if name in env.vars:
                return env.vars[name]
            env = env.parent
        return None

    def declare(self, name, value):
        self.vars[name] = value if isinstance(value, (Cell, RefCell, Proc)) else Cell(value)


class Proc:
    def __init__(self, name, params, body, env, is_function):
        self.name, self.params, self.body, self.env, self.is_function = name, params, body, env, is_function


class Signal(Exception):
    pass


class Exit(Signal):
    pass


class Break(Signal):
    """break, or break(LABEL) out of the loop written `for [LABEL] ...` / `while [LABEL] ...`."""

    def __init__(self, label=None):
        super().__init__(label)
        self.label = label


class Continue(Break):
    pass


def _own(signal, label):
    """Does this loop handle a break / continue? (an unlabelled one: the innermost loop)"""
    return signal.label is None or signal.label == label


# ---------------------------------------------------------------- parser

class Parser:
    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self, k=0):
        return self.t[self.i + k]

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def at(self, kind, value=None):
        tok = self.t[self.i]
        return tok[0] == kind and (value is None or tok[1] == value)

    def accept(self, kind, value=None):
        if self.at(kind, value):
            self.i += 1
            return True
        return False

    def expect(self, kind, value=None):
        if not self.at(kind, value):
            raise SyntaxError(f"expected {kind} {value!r}, got {self.peek()} at token {self.i}")
        return self.next()

    # -- declarations and statements

    def unit(self):
        """Top level: procedure and function definitions (anything else is skipped)."""
        items = []
        while not self.at("eof"):
            if self.at("kw", "procedure") or self.at("kw", "function"):
                items.append(self.proc_def())
            else:
                self.next()
        return items

    def proc_def(self):
        is_function = self.next()[1] == "function"
        name = self.expect("id")[1]
        params = []
        if self.accept("sym", "("):
            while not self.accept("sym", ")"):
                mode = "value"
                if self.accept("kw", "var"):
                    mode = "var"
                elif self.accept("kw", "const"):
                    mode = "const"
                names = [self.expect("id")[1]]
                while self.accept("sym", ","):
                    names.append(self.expect("id")[1])
                typ = None
                if self.accept("sym", ":"):
                    typ = self.type_spec(stop={";", ")"})
                for n in names:
                    params.append((n, mode, typ))
                self.accept("sym", ";")
        if self.accept("sym", ":"):
            self.type_spec(stop={";"})
        self.accept("sym", ";")
        body = self.block()
        self.accept("sym", ";")
        return ("procdef", name, params, body, is_function)

    def type_spec(self, stop):
        """Skip a type, return it as text (enough to pick a default value)."""
        parts, depth = [], 0
        while True:
            tok = self.peek()
            if tok[0] == "eof":
                break
            if tok[0] == "sym" and tok[1] in ("(", "["):
                depth += 1
            elif tok[0] == "sym" and tok[1] in (")", "]"):
                if depth == 0:
                    break
                depth -= 1
            elif depth == 0 and ((tok[0] == "sym" and tok[1] in stop) or (tok[0] == "sym" and tok[1] == "=")):
                break
            parts.append(str(tok[1]))
            self.next()
        return " ".join(parts).lower()

    def block(self):
        self.expect("kw", "begin")
        stmts = self.stmts({"end"})
        self.expect("kw", "end")
        return ("block", stmts)

    def stmts(self, stop):
        out = []
        while not (self.at("kw") and self.peek()[1] in stop) and not self.at("eof"):
            s = self.stmt()
            if s is not None:
                out.append(s)
            self.accept("sym", ";")
        return out

    def loop_label(self):
        """The optional `[LABEL]` after for / while, the target of break(LABEL)."""
        if self.at("sym", "[") and self.peek(1)[0] == "id" and self.peek(2) == ("sym", "]") \
                and self.peek(3) != ("kw", "in"):
            self.next()
            label = self.next()[1]
            self.next()
            return label
        return None

    def stmt(self):
        tok = self.peek()
        if tok == ("sym", ";"):
            return None
        if tok[0] == "kw":
            kw = tok[1]
            if kw == "begin":
                return self.block()
            if kw == "if":
                self.next()
                cond = self.expr()
                self.expect("kw", "then")
                then = self.stmt() if not self.at("kw", "else") else None
                other = None
                if self.accept("kw", "else"):
                    other = self.stmt()
                return ("if", cond, then, other)
            if kw == "case":
                return self.case_stmt()
            if kw == "for":
                self.next()
                label = self.loop_label()
                var = self.expect("id")[1]
                self.expect("sym", ":=")
                start = self.expr()
                down = self.next()[1] == "downto"
                stop = self.expr()
                self.expect("kw", "do")
                return ("for", var, start, stop, down, self.stmt(), label)
            if kw == "while":
                self.next()
                label = self.loop_label()
                cond = self.expr()
                self.expect("kw", "do")
                return ("while", cond, self.stmt(), label)
            if kw == "repeat":
                self.next()
                body = self.stmts({"until"})
                self.expect("kw", "until")
                return ("repeat", ("block", body), self.expr())
            if kw in ("var", "const"):
                return self.decl(kw)
            if kw == "type":
                self.next()
                self.expect("id")
                self.expect("sym", "=")
                if self.accept("kw", "class") or self.accept("kw", "record"):
                    depth = 1
                    while depth and not self.at("eof"):
                        tok = self.next()
                        if tok[0] == "kw" and tok[1] in ("begin", "case", "class", "record"):
                            depth += 1
                        elif tok == ("kw", "end"):
                            depth -= 1
                else:
                    self.type_spec(stop={";"})
                return None
            if kw in ("procedure", "function"):
                return self.proc_def()
            if kw == "exit":
                self.next()
                return ("exit",)
            if kw in ("break", "continue"):
                self.next()
                label = None
                if self.accept("sym", "("):
                    label = self.expect("id")[1]
                    self.expect("sym", ")")
                return (kw, label)
            raise SyntaxError(f"unexpected {tok} at token {self.i}")
        target = self.designator()
        if self.accept("sym", ":="):
            return ("assign", target, self.expr())
        return ("call", target)

    def decl(self, kw):
        self.next()
        names = [self.expect("id")[1]]
        while self.accept("sym", ","):
            names.append(self.expect("id")[1])
        typ = None
        if self.accept("sym", ":"):
            typ = self.type_spec(stop={";"})
        value = None
        if self.accept("sym", "="):
            value = self.expr()
        return ("decl", names, typ, value)

    def case_stmt(self):
        self.expect("kw", "case")
        subject = self.expr()
        self.expect("kw", "of")
        branches, other = [], None
        while not self.at("kw", "end"):
            if self.accept("kw", "else"):
                other = ("block", self.stmts({"end"}))
                break
            labels = [self.case_label()]
            while self.accept("sym", ","):
                labels.append(self.case_label())
            self.expect("sym", ":")
            branches.append((labels, self.stmt()))
            self.accept("sym", ";")
        self.expect("kw", "end")
        return ("case", subject, branches, other)

    def case_label(self):
        low = self.expr()
        if self.accept("sym", ".."):
            return ("range", low, self.expr())
        return low

    # -- expressions

    def expr(self):
        left = self.simple()
        while self.peek() in (("sym", "="), ("sym", "<>"), ("sym", "<"), ("sym", ">"), ("sym", "<="), ("sym", ">="),
                              ("kw", "in")):
            op = self.next()[1]
            left = ("bin", op, left, self.simple())
        return left

    def simple(self):
        left = self.term()
        while self.peek() in (("sym", "+"), ("sym", "-"), ("kw", "or"), ("kw", "xor")):
            op = self.next()[1]
            left = ("bin", op, left, self.term())
        return left

    def term(self):
        left = self.factor()
        while self.peek() in (("sym", "*"), ("sym", "/"), ("kw", "div"), ("kw", "mod"), ("kw", "and"),
                              ("kw", "shl"), ("kw", "shr")):
            op = self.next()[1]
            left = ("bin", op, left, self.factor())
        return left

    def factor(self):
        tok = self.peek()
        if tok[0] in ("num", "str"):
            self.next()
            node = ("lit", tok[1])
            while self.at("str") and tok[0] == "str":  # 'a'#9'b' concatenation
                node = ("bin", "+", node, ("lit", self.next()[1]))
            return node
        if tok == ("kw", "not"):
            self.next()
            return ("not", self.factor())
        if tok == ("sym", "-"):
            self.next()
            return ("neg", self.factor())
        if tok == ("sym", "+"):
            self.next()
            return self.factor()
        if tok == ("sym", "("):
            self.next()
            e = self.expr()
            self.expect("sym", ")")
            return e
        if tok == ("sym", "["):  # set literal: only used with "in"
            self.next()
            items = []
            while not self.accept("sym", "]"):
                items.append(self.expr())
                self.accept("sym", ",")
            return ("set", items)
        return self.designator()

    def designator(self):
        node = ("name", self.expect("id")[1])
        while True:
            if self.accept("sym", "."):
                node = ("field", node, self.expect("id")[1])
            elif self.accept("sym", "["):
                keys = [self.expr()]
                while self.accept("sym", ","):
                    keys.append(self.expr())
                self.expect("sym", "]")
                for k in keys:
                    node = ("index", node, k)
            elif self.accept("sym", "("):
                args = []
                while not self.accept("sym", ")"):
                    args.append(self.expr())
                    self.accept("sym", ",")
                node = ("callx", node, args)
            else:
                return node


# ---------------------------------------------------------------- interpreter

def default_for(typ):
    typ = (typ or "").strip()
    if typ in ("integer", "word", "byte", "cardinal", "longint", "int64", "float", "single", "double", "real"):
        return 0
    if typ == "string":
        return ""
    if typ == "boolean":
        return False
    if typ == "pointer":
        return None
    return Box()


class Interpreter:
    def __init__(self, sources, constants=None, natives=None):
        self.globals = Env()
        self.natives = dict(BUILTINS)
        self.natives.update(natives or {})
        self.unknown = {}
        for name, value in (constants or {}).items():
            self.globals.declare(name.lower(), value)
        for src in sources:
            for item in Parser(tokenize(src)).unit():
                self.define(item, self.globals)

    def define(self, node, env):
        _, name, params, body, is_function = node
        env.declare(name, Proc(name, params, body, env, is_function))

    # -- calls

    def call(self, name, args):
        proc = self.globals.find(name.lower())
        return self.invoke(proc, [lambda v=a: v for a in args], [None] * len(args))

    def invoke(self, proc, arg_values, arg_refs):
        env = Env(proc.env)
        for i, (pname, mode, typ) in enumerate(proc.params):
            if i >= len(arg_values):
                env.declare(pname, default_for(typ))
            elif mode == "var" and arg_refs[i] is not None:
                env.declare(pname, arg_refs[i])
            else:
                env.declare(pname, arg_values[i]())
        if proc.is_function:
            env.declare("result", 0)
        try:
            self.exec(proc.body, env)
        except Exit:
            pass
        return env.vars["result"].get() if proc.is_function else None

    # -- statements

    def exec(self, node, env):
        kind = node[0]
        if kind == "block":
            for s in node[1]:
                self.exec(s, env)
        elif kind == "assign":
            self.assign(node[1], self.eval(node[2], env), env)
        elif kind == "call":
            self.eval(node[1], env, statement=True)
        elif kind == "if":
            if self.truth(self.eval(node[1], env)):
                if node[2] is not None:
                    self.exec(node[2], env)
            elif node[3] is not None:
                self.exec(node[3], env)
        elif kind == "decl":
            _, names, typ, value = node
            for n in names:
                env.declare(n, self.eval(value, env) if value is not None else default_for(typ))
        elif kind == "procdef":
            self.define(node, env)
        elif kind == "for":
            _, var, start, stop, down, body, label = node
            cell = env.find(var)
            if cell is None:
                env.declare(var, 0)
                cell = env.find(var)
            a, b = num(self.eval(start, env)), num(self.eval(stop, env))
            rng = range(a, b - 1, -1) if down else range(a, b + 1)
            for v in rng:
                cell.set(v)
                try:
                    self.exec(body, env)
                except Continue as c:
                    if not _own(c, label):
                        raise
                except Break as b:
                    if not _own(b, label):
                        raise
                    break
        elif kind == "while":
            guard = 0
            while self.truth(self.eval(node[1], env)):
                guard += 1
                if guard > 1_000_000:
                    raise RuntimeError("endless while")
                try:
                    self.exec(node[2], env)
                except Continue as c:
                    if not _own(c, node[3]):
                        raise
                except Break as b:
                    if not _own(b, node[3]):
                        raise
                    break
        elif kind == "repeat":
            while True:
                try:
                    self.exec(node[1], env)
                except Continue as c:
                    if c.label is not None:
                        raise
                except Break as b:
                    if b.label is not None:
                        raise
                    break
                if self.truth(self.eval(node[2], env)):
                    break
        elif kind == "case":
            _, subject, branches, other = node
            value = plain(self.eval(subject, env))
            for labels, body in branches:
                if any(self.case_match(value, lab, env) for lab in labels):
                    if body is not None:
                        self.exec(body, env)
                    break
            else:
                if other is not None:
                    self.exec(other, env)
        elif kind == "exit":
            raise Exit()
        elif kind == "break":
            raise Break(node[1])
        elif kind == "continue":
            raise Continue(node[1])
        else:
            raise RuntimeError(f"cannot execute {kind}")

    def case_match(self, value, label, env):
        if label[0] == "range":
            return num(self.eval(label[1], env)) <= num(value) <= num(self.eval(label[2], env))
        return plain(self.eval(label, env)) == value

    @staticmethod
    def truth(v):
        v = plain(v)
        return bool(v) and v is not EMPTY

    # -- locations

    def locate(self, node, env):
        """(getter, setter) of an assignable place."""
        kind = node[0]
        if kind == "name":
            name = node[1]
            cell = env.find(name)
            if cell is None or isinstance(cell, Proc):
                cell = Cell(EMPTY)
                self.globals.vars[name] = cell  # an engine global: remember what the script writes to it
            return cell.get, cell.set
        if kind == "field":
            base = self.container(node[1], env)
            name = node[2]
            return (lambda: base.get_field(name)), (lambda v: base.fields.__setitem__(name, v))
        if kind == "index":
            base = self.container(node[1], env)
            key = self.key(self.eval(node[2], env))
            return (lambda: base.get_item(key)), (lambda v: base.items.__setitem__(key, v))
        if kind == "callx":
            return (lambda: self.eval(node, env)), (lambda v: None)
        raise RuntimeError(f"not assignable: {kind}")

    def container(self, node, env):
        getter, setter = self.locate(node, env)
        value = getter()
        if not isinstance(value, Box):
            value = Box()
            setter(value)
        return value

    @staticmethod
    def key(v):
        v = plain(v)
        return 0 if v is EMPTY else v

    def assign(self, target, value, env):
        if isinstance(value, _Empty):
            value = EMPTY
        self.locate(target, env)[1](value)

    # -- expressions

    def eval(self, node, env, statement=False):
        kind = node[0]
        if kind == "lit":
            return node[1]
        if kind == "name":
            name = node[1]
            cell = env.find(name)
            if isinstance(cell, Proc):
                return self.invoke(cell, [], [])
            if cell is None:
                if name in ("true", "false"):
                    return name == "true"
                if name == "nil":
                    return None
                if name in self.natives:
                    return self.natives[name](self, [])
                self.unknown[name] = self.unknown.get(name, 0) + 1
                return EMPTY
            return cell.get()
        if kind in ("field", "index"):
            return self.locate(node, env)[0]()
        if kind == "callx":
            return self.call_node(node, env)
        if kind == "not":
            v = plain(self.eval(node[1], env))
            return (~v) if isinstance(v, int) and not isinstance(v, bool) else not self.truth(v)
        if kind == "neg":
            return -num(self.eval(node[1], env))
        if kind == "set":
            return {plain(self.eval(x, env)) for x in node[1]}
        if kind == "bin":
            return self.binary(node[1], node[2], node[3], env)
        raise RuntimeError(f"cannot evaluate {kind}")

    def binary(self, op, left_node, right_node, env):
        if op == "and":
            left = plain(self.eval(left_node, env))
            if isinstance(left, bool) or left is EMPTY:
                return self.truth(left) and self.truth(self.eval(right_node, env))
            return num(left) & num(self.eval(right_node, env))
        if op == "or":
            left = plain(self.eval(left_node, env))
            if isinstance(left, bool) or left is EMPTY:
                return self.truth(left) or self.truth(self.eval(right_node, env))
            return num(left) | num(self.eval(right_node, env))
        a, b = plain(self.eval(left_node, env)), plain(self.eval(right_node, env))
        if op == "+":
            if isinstance(a, str) or isinstance(b, str):
                return text(a) + text(b)
            return num(a) + num(b)
        if op == "-":
            return num(a) - num(b)
        if op == "*":
            return num(a) * num(b)
        if op == "/":
            return num(a) / num(b) if num(b) else 0
        if op == "div":
            return int(num(a) / num(b)) if num(b) else 0
        if op == "mod":
            return num(a) % num(b) if num(b) else 0
        if op == "shl":
            return num(a) << num(b)
        if op == "shr":
            return num(a) >> num(b)
        if op == "xor":
            return (num(a) ^ num(b)) if not isinstance(a, bool) else (self.truth(a) != self.truth(b))
        if op == "in":
            return a in b if isinstance(b, set) else False
        if isinstance(a, str) or isinstance(b, str):
            a, b = text(a), text(b)
        elif not isinstance(a, bool) or not isinstance(b, bool):
            if a is EMPTY or b is EMPTY or isinstance(a, (int, float)) or isinstance(b, (int, float)):
                a, b = num(a), num(b)
        return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]

    def call_node(self, node, env):
        _, target, args = node
        if target[0] != "name":
            return EMPTY  # method calls on objects: nothing we need
        name = target[1]
        proc = env.find(name)
        if isinstance(proc, Proc):
            values, refs = [], []
            for i, a in enumerate(args):
                mode = proc.params[i][1] if i < len(proc.params) else "value"
                if mode == "var" and a[0] in ("name", "field", "index"):
                    getter, setter = self.locate(a, env)
                    refs.append(RefCell(getter, setter))
                    values.append(getter)
                else:
                    refs.append(None)
                    values.append((lambda a=a: self.eval(a, env)))
            return self.invoke(proc, values, refs)
        native = self.natives.get(name)
        if native is not None:
            return native(self, [_Arg(self, a, env) for a in args])
        self.unknown[name] = self.unknown.get(name, 0) + 1
        return EMPTY


class _Arg:
    """A native function's argument: its value, and a way to write back (var parameters)."""

    def __init__(self, interp, node, env):
        self.interp, self.node, self.env = interp, node, env

    @property
    def value(self):
        return plain(self.interp.eval(self.node, self.env))

    @property
    def raw(self):
        return self.interp.eval(self.node, self.env)

    def set(self, value):
        self.interp.assign(self.node, value, self.env)


def _substr(s, start, length):
    start = max(1, num(start))
    return text(s)[start - 1:start - 1 + max(0, num(length))]


BUILTINS = {
    "inttostr": lambda it, a: str(int(num(a[0].value))),
    "floattostr": lambda it, a: str(num(a[0].value)),
    "strtoint": lambda it, a: int(text(a[0].value) or 0),
    "strlength": lambda it, a: len(text(a[0].value)),
    "substr": lambda it, a: _substr(a[0].value, a[1].value, a[2].value),
    "strexists": lambda it, a: text(a[1].value) in text(a[0].value),
    "strreplace": lambda it, a: text(a[0].value).replace(text(a[1].value), text(a[2].value)),
    "strpos": lambda it, a: text(a[1].value).find(text(a[0].value)) + 1,
    "lowercase": lambda it, a: text(a[0].value).lower(),
    "uppercase": lambda it, a: text(a[0].value).upper(),
    "min": lambda it, a: min(num(a[0].value), num(a[1].value)),
    "max": lambda it, a: max(num(a[0].value), num(a[1].value)),
    "abs": lambda it, a: abs(num(a[0].value)),
    "round": lambda it, a: int(round(num(a[0].value))),
    "floor": lambda it, a: int(num(a[0].value) // 1),
    "trunc": lambda it, a: int(num(a[0].value)),
    "log": lambda it, a: None,
    "errorlog": lambda it, a: None,
}


def read_constants(dmscript_global):
    """name -> value from data/scripts/dmscript.global ("gc_x = <expr>;" lines)."""
    consts = {}
    interp = Interpreter([], {})
    pending = []
    for line in dmscript_global.splitlines():
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?);\s*(//.*)?$", line)
        if m:
            pending.append((m.group(1).lower(), m.group(2)))
    for _ in range(3):  # constants may refer to later ones
        for name, expr in pending:
            try:
                p = Parser(tokenize(expr))
                value = interp.eval(p.expr(), interp.globals)
            except (SyntaxError, RuntimeError, TypeError, ZeroDivisionError):
                continue
            if value is not EMPTY:
                consts[name] = value
                interp.globals.declare(name, value)
    return consts
