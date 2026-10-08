"""Fixed packaged entry point. No user-supplied commands or development server."""
import multiprocessing
import os


def main():
    multiprocessing.freeze_support()
    import uvicorn
    from app.main import create_app
    from app.core.config import Settings
    config=Settings(_env_file=None)
    if not config.native_session_token:
        raise RuntimeError("A native session is required")
    uvicorn.run(create_app(config),host="127.0.0.1",port=config.port,access_log=False,log_level="warning")


if __name__=="__main__":main()
