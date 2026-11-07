"""Local read-only utilities alongside the existing confirmed shopping MCP."""
import ast,operator,math,os,platform,asyncio
from datetime import datetime,timezone,timedelta
from decimal import Decimal,localcontext,InvalidOperation
from pathlib import Path
from task_mcp.server import make_server
from speech_mcp.contracts import identity
ROOT=Path(__file__).resolve().parents[1]
OPS={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv}
def calculate(expression):
    if not isinstance(expression,str) or not 1<=len(expression)<=200:raise ValueError('invalid_expression')
    try:
        tree=ast.parse(expression,mode='eval')
        if len(list(ast.walk(tree)))>64:raise ValueError('expression_limit')
        def evaluate(node,depth=0):
            if depth>12:raise ValueError('expression_limit')
            if isinstance(node,ast.Constant) and type(node.value) in {int,float}:
                value=Decimal(str(node.value))
            elif isinstance(node,ast.UnaryOp) and type(node.op) in {ast.UAdd,ast.USub}:
                value=evaluate(node.operand,depth+1)*(1 if isinstance(node.op,ast.UAdd) else -1)
            elif isinstance(node,ast.BinOp) and type(node.op) in OPS:
                value=OPS[type(node.op)](evaluate(node.left,depth+1),evaluate(node.right,depth+1))
            else:raise ValueError('unsupported_expression')
            if not value.is_finite() or abs(value)>Decimal('1e15'):raise ValueError('expression_limit')
            return value
        with localcontext() as ctx:
            ctx.prec=28;value=evaluate(tree.body)
            return {'expression':expression,'result':format(value,'f'),'precision_digits':28,'rounded':True,'read_only':True}
    except (SyntaxError,ArithmeticError,InvalidOperation,RecursionError):raise ValueError('invalid_expression') from None
def current_time(utc_offset_minutes=480):
    if type(utc_offset_minutes) is not int or not -720<=utc_offset_minutes<=840:raise ValueError('invalid_utc_offset')
    now=datetime.now(timezone(timedelta(minutes=utc_offset_minutes)))
    return {'iso_time':now.isoformat(timespec='seconds'),'utc_offset_minutes':utc_offset_minutes,'source':'Pi operating system clock; not independently NTP-certified','read_only':True}
def server(path,utility_delay_ms=0):
    if type(utility_delay_ms) is not int or not 0<=utility_delay_ms<=20000:raise ValueError('invalid_controlled_delay')
    app=make_server(path)
    def perform(generation,request_id,operation):
        identity(generation,request_id)
        info={'generation':generation,'request_id':request_id,'server_role':'task','server_pid':os.getpid(),'host':platform.system(),'machine':platform.machine()}
        try:return {'ok':True,**info,**operation()}
        except ValueError:return {'ok':False,**info,'error':'invalid_utility_arguments'}
    from pydantic import StrictInt
    @app.tool()
    async def calculate_expression(expression:str,generation:StrictInt,request_id:str)->dict:
        """Evaluate bounded decimal + - * / arithmetic. No code, files, network, or side effects."""
        if utility_delay_ms:await asyncio.sleep(utility_delay_ms/1000)
        return perform(generation,request_id,lambda:calculate(expression))
    @app.tool()
    async def get_current_time(utc_offset_minutes:StrictInt,generation:StrictInt,request_id:str)->dict:
        """Read the Pi clock at an explicit UTC offset in minutes; Manila is 480. No clock changes."""
        if utility_delay_ms:await asyncio.sleep(utility_delay_ms/1000)
        return perform(generation,request_id,lambda:current_time(utc_offset_minutes))
    return app
def main():
    import argparse,logging
    p=argparse.ArgumentParser();p.add_argument('--test-db');p.add_argument('--utility-delay-ms',type=int,default=0);a=p.parse_args();path=ROOT/'data/shopping.sqlite3'
    if a.test_db:
        path=Path(a.test_db).resolve()
        if not path.is_relative_to((ROOT/'.tmp').resolve()):raise ValueError('test_database_outside_project_tmp')
    logging.disable(logging.CRITICAL);server(path,a.utility_delay_ms).run(transport='stdio')
if __name__=='__main__':main()
