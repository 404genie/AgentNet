from fastapi import FastAPI
import random
import time

app = FastAPI()

@app.post("/execute")
async def execute(payload: dict):
    delay = random.uniform(0.5, 2)
    time.sleep(delay)

    if random.random() < 0.2:
        return {"status": "failed"}

    return {"status": "success", "result": "done"}