"""Combine several input series with a simple formula (spec §4.1, `source: derived`)."""

import ast
import operator

import pandas as pd

from dashboard.config import Indicator

BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def _evaluate(node, values: dict[str, pd.Series]):
    match node:
        case ast.Expression(body=body):
            return _evaluate(body, values)
        case ast.BinOp(left=left, op=op, right=right):
            return BINOPS[type(op)](_evaluate(left, values), _evaluate(right, values))
        case ast.UnaryOp(op=ast.USub(), operand=operand):
            return -_evaluate(operand, values)
        case ast.Name(id=name):
            return values[name]
        case ast.Constant(value=value):
            return value
    raise ValueError(f"unsupported formula element {ast.dump(node)}")


def combine(ind: Indicator, inputs: dict[str, pd.Series]) -> pd.Series:
    """Evaluate the formula on the dates of the first input; other inputs use their latest
    value on or before each date (so a quarterly GDP figure applies to the months after it)."""
    from dashboard.signals import apply_transform  # avoid import cycle

    prepared = {}
    for spec in ind.inputs:
        s = inputs[spec.name].dropna().sort_index()
        for name in spec.transform:
            s = apply_transform(s, name)
        prepared[spec.name] = s.dropna()
    base = prepared[ind.inputs[0].name].index
    aligned = {name: pd.Series(s.asof(base).values, index=base) for name, s in prepared.items()}
    result = _evaluate(ast.parse(ind.formula, mode="eval"), aligned)
    return result.replace([float("inf"), float("-inf")], float("nan")).dropna()
