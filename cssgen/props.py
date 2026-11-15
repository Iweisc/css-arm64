"""Registers custom properties (@property) and collects declarations into rule blocks."""
from .expr import V


class Registry:
    def __init__(self):
        self.props = {}
        # Names output as inherited whatever they were registered as: the CPU's own values, which
        # flow down its tree of slots (see cssgen.page.stylesheet).
        self.inherited = set()

    def reg(self, name, inherits=False, initial=0, syntax="<integer>"):
        spec = (syntax, inherits, initial)
        old = self.props.get(name)
        if old is not None and old != spec:
            raise ValueError(f"--{name} registered twice with different specs: {old} vs {spec}")
        self.props[name] = spec

    def css(self):
        out = []
        for name, (syntax, inherits, initial) in self.props.items():
            inherits = inherits or name in self.inherited
            out.append(f"@property --{name}{{syntax:'{syntax}';inherits:{'true' if inherits else 'false'};"
                       f"initial-value:{initial}}}")
        return "\n".join(out)


REG = Registry()


class Block:
    """Declarations for one rule. let() registers the property and returns var(--name)."""

    def __init__(self, inherits=False):
        self.decls = []
        self.inherits = inherits

    def let(self, name, expr, inherits=None, initial=0):
        REG.reg(name, self.inherits if inherits is None else inherits, initial)
        # Always wrap: a bare "var(--a) + 1" is not a valid value, only calc() makes it arithmetic.
        self.decls.append((f"--{name}", f"calc({expr})"))
        return V(name)

    def raw(self, prop, value):
        self.decls.append((prop, value))

    def css(self, selector):
        body = ";".join(f"{p}:{v}" for p, v in self.decls)
        return f"{selector}{{{body}}}"
