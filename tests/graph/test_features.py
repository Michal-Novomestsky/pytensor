import pytest

import pytensor.tensor as pt
from pytensor.graph import rewrite_graph
from pytensor.graph.basic import Apply, Variable, equal_computations
from pytensor.graph.features import Feature, FullHistory, NodeFinder, ReplaceValidate
from pytensor.graph.fg import FunctionGraph
from pytensor.graph.op import Op
from pytensor.graph.type import Type
from tests.graph.utils import MyVariable, op1


class TestNodeFinder:
    def test_straightforward(self):
        class MyType(Type):
            def __init__(self, name):
                self.name = name

            def filter(self, *args, **kwargs):
                raise NotImplementedError()

            def __str__(self):
                return self.name

            def __repr__(self):
                return self.name

            def __eq__(self, other):
                return isinstance(other, MyType)

        class MyOp(Op):
            __props__ = ("nin", "name")

            def __init__(self, nin, name):
                self.nin = nin
                self.name = name

            def make_node(self, *inputs):
                def as_variable(x):
                    assert isinstance(x, Variable)
                    return x

                assert len(inputs) == self.nin
                inputs = list(map(as_variable, inputs))
                for input in inputs:
                    if not isinstance(input.type, MyType):
                        raise Exception("Error 1")
                outputs = [MyType(self.name + "_R")()]
                return Apply(self, inputs, outputs)

            def __str__(self):
                return self.name

            def perform(self, *args, **kwargs):
                raise NotImplementedError()

        sigmoid = MyOp(1, "Sigmoid")
        add = MyOp(2, "Add")
        dot = MyOp(2, "Dot")

        def MyVariable(name):
            return Variable(MyType(name), None, None)

        def inputs():
            x = MyVariable("x")
            y = MyVariable("y")
            z = MyVariable("z")
            return x, y, z

        x, y, z = inputs()
        e0 = dot(y, z)
        e = add(add(sigmoid(x), sigmoid(sigmoid(z))), dot(add(x, y), e0))
        g = FunctionGraph([x, y, z], [e], clone=False)
        g.attach_feature(NodeFinder())

        assert hasattr(g, "get_nodes")
        for type, num in ((add, 3), (sigmoid, 3), (dot, 2)):
            if len(list(g.get_nodes(type))) != num:
                raise Exception(f"Expected: {num} times {type}")
        new_e0 = add(y, z)
        assert e0.owner in g.get_nodes(dot)
        assert new_e0.owner not in g.get_nodes(add)
        g.replace(e0, new_e0)
        assert e0.owner not in g.get_nodes(dot)
        assert new_e0.owner in g.get_nodes(add)
        for type, num in ((add, 4), (sigmoid, 3), (dot, 1)):
            if len(list(g.get_nodes(type))) != num:
                raise Exception(f"Expected: {num} times {type}")


class TestReplaceValidate:
    def test_verbose(self, capsys):
        var1 = MyVariable("var1")
        var2 = MyVariable("var2")
        var3 = op1(var2, var1)
        fg = FunctionGraph([var1, var2], [var3], clone=False)

        rv_feature = ReplaceValidate()
        fg.attach_feature(rv_feature)
        rv_feature.replace_all_validate(
            fg, [(var3, var1)], reason="test-reason", verbose=True
        )

        capres = capsys.readouterr()
        assert capres.err == ""
        assert (
            "rewriting: rewrite test-reason replaces Op1.0 of Op1(var2, var1) with var1 of None"
            in capres.out
        )

        class TestFeature(Feature):
            def validate(self, *args):
                raise Exception()

        fg.attach_feature(TestFeature())

        with pytest.raises(Exception):
            rv_feature.replace_all_validate(
                fg, [(var3, var1)], reason="test-reason", verbose=True
            )

        capres = capsys.readouterr()
        assert "rewriting: validate failed on node Op1.0" in capres.out


def test_full_history():
    x = pt.scalar("x")
    out = pt.log(pt.exp(x) / pt.sum(pt.exp(x)))
    fg = FunctionGraph(outputs=[out], clone=True, copy_inputs=False)
    history = FullHistory()
    fg.attach_feature(history)
    rewrite_graph(fg, clone=False, include=("canonicalize", "stabilize"))

    history.start()
    assert equal_computations(fg.outputs, [out])

    history.end()
    assert equal_computations(fg.outputs, [pt.special.log_softmax(x)])

    history.prev()
    assert equal_computations(fg.outputs, [pt.log(pt.special.softmax(x))])

    for i in range(10):
        history.prev()
    assert equal_computations(fg.outputs, [out])

    history.goto(2)
    assert equal_computations(fg.outputs, [pt.log(pt.special.softmax(x))])

    for i in range(10):
        history.next()

    assert equal_computations(fg.outputs, [pt.special.log_softmax(x)])
