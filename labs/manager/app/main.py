from fastapi import FastAPI

app = FastAPI(title="Learning Factory lab manager")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}
