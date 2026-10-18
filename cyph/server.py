"""Entry point for the 24/7 server: python -m cyph.server

Deliberately separate from cyph.__main__, which imports the arm, camera and audio code the server does not have.
"""
import sys


def main():
    if "--check" in sys.argv:                          # imports and config only; never polls Telegram
        from . import agent, digest, memory, proactive, router, schedule, summary, telegram   # noqa: F401
        from .keys import load_gemini_key
        print("imports ok | gemini key:", "set" if load_gemini_key() else "MISSING",
              "| telegram token:", "set" if telegram.load_token() else "MISSING")
        return
    from .telegram import run_service
    run_service(None, server=True)


if __name__ == "__main__":
    main()
