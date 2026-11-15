"""Run the browser workflow with an isolated local application instance."""
from pathlib import Path
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix='platform-b-v4-') as temp:
        env=os.environ.copy();env.update(WORKBENCH_HOST='127.0.0.1',WORKBENCH_RUNS_DIR=str(Path(temp)/'runs'),WORKBENCH_URL=f'http://127.0.0.1:{port}',PYTHONIOENCODING='utf-8')
        for key in ['LLM_BASE_URL','LLM_API_KEY','METHOD_ENDPOINT','RISK_ENDPOINT','HALLUCINATION_ENDPOINT','TOPIC2_MODEL_ENDPOINT','PROOPF_BASE_URL','REWARD_ENDPOINT','SOLVER_CMD']:
            env[key]=''
        with (Path(temp)/'server.log').open('w',encoding='utf-8') as log:
            server=subprocess.Popen([sys.executable,'run.py','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
            try:
                for _ in range(60):
                    if server.poll() is not None:raise RuntimeError('Application exited before startup')
                    try:
                        with urllib.request.urlopen(env['WORKBENCH_URL']+'/api/health',timeout=1) as r:
                            if r.status==200:break
                    except Exception:time.sleep(.25)
                else:raise RuntimeError('Application startup timed out')
                result=subprocess.run(['node','tests/workspace-v4.e2e.mjs'],cwd=ROOT/'frontend',env=env,timeout=240)
                return result.returncode
            finally:
                server.terminate()
                try:server.wait(timeout=10)
                except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
if __name__=='__main__':raise SystemExit(main())
