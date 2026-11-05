"""Bounded arithmetic evaluator with no names, calls, files or Python eval."""
import ast
from decimal import Decimal,DecimalException,localcontext


def calculate(expression):
    if not isinstance(expression,str) or not expression.strip() or len(expression)>4096:
        raise ValueError('Arithmetic expression must be1–4096 characters')
    try:
        tree=ast.parse(expression,mode='eval')
    except (SyntaxError,RecursionError):
        raise ValueError('Invalid arithmetic expression') from None
    nodes=0
    def visit(node,depth=0):
        nonlocal nodes
        nodes+=1
        if nodes>500 or depth>40:
            raise ValueError('Arithmetic complexity limit exceeded')
        if isinstance(node,ast.Expression):
            return visit(node.body,depth+1)
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):
            value=Decimal(str(node.value))
        elif isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            value=visit(node.operand,depth+1)
            if isinstance(node.op,ast.USub):value=-value
        elif isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub,ast.Mult,ast.Div,ast.FloorDiv)):
            left,right=visit(node.left,depth+1),visit(node.right,depth+1)
            if isinstance(node.op,ast.Add):value=left+right
            elif isinstance(node.op,ast.Sub):value=left-right
            elif isinstance(node.op,ast.Mult):value=left*right
            elif isinstance(node.op,ast.Div):value=left/right
            else:value=left//right
        else:
            raise ValueError('Only numeric constants, parentheses, +, -, *, / and // are allowed')
        if not value.is_finite() or abs(value)>Decimal('1e40'):
            raise ValueError('Arithmetic magnitude limit exceeded')
        return value
    try:
        with localcontext() as context:
            context.prec=40
            value=visit(tree)
            return {'value':int(value) if value==value.to_integral_value() else format(value,'f')}
    except (DecimalException,OverflowError,RecursionError):
        raise ValueError('Arithmetic result is undefined or exceeds limits') from None
