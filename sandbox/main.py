"""
Isolated code-execution sidecar. Runs an AI-generated Python snippet as a
separate OS process (subprocess, not exec()-ed in this process) and hands
back only its stdout/exit status. The real security boundary is outside
this file: docker-compose.yml puts this service's container on the
`baseera-private` network with `internal: true` (no outbound internet
access at all) and does not mount any of the app's secrets into it, so
even code that reads every file this process can see has nothing to phone
home to and nothing sensitive to find. See
dashboard/services/sandbox_client.py for the caller side of this contract.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess
import tempfile
import os
import json

app = FastAPI(title="Baseera Python Sandbox")


class CodeExecutionRequest(BaseModel):
    code: str
    timeout_seconds: int = 10


@app.post("/run")
async def run_code(request: CodeExecutionRequest):
    """
    Executes the submitted code as a standalone subprocess and returns its
    output. Isolation is at the container/network level (see module
    docstring) -- this endpoint itself only bounds execution time and
    cleans up the temporary script file.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as temp_script:
        temp_script.write(request.code)
        temp_script_path = temp_script.name

    try:
        result = subprocess.run(
            ["python", temp_script_path],
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
        )

        output = result.stdout
        error = result.stderr

        # The AI-generated code is expected to print plain text or JSON.
        # If it's valid JSON, parse it so the caller gets a structured
        # value back; otherwise pass the raw string through untouched.
        try:
            parsed_output = json.loads(output) if output else None
        except json.JSONDecodeError:
            parsed_output = output

        return {
            "success": result.returncode == 0,
            "output": parsed_output,
            "error": error if error else None,
        }

    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=408,
            detail=f"Execution timed out after {request.timeout_seconds} seconds.",
        )
    finally:
        if os.path.exists(temp_script_path):
            os.remove(temp_script_path)


@app.get("/health")
async def health_check():
    return {"status": "Sandbox is ready"}
