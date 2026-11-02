"""Run the game with `python -m seneschal`, or a transcript playtest with `python -m seneschal playtest`."""
import argparse
from pathlib import Path

from .llm import Settings
from .playtest import SCENARIOS, run_all
from .server import serve


def main():
    parser = argparse.ArgumentParser(prog="seneschal", description="A storyteller for one player.")
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "playtest"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", default=".seneschal", help="Where chronicles and settings are saved")
    parser.add_argument("--env-file", default=".env", help="File holding MISTRAL_API_KEY and/or OPENAI_API_KEY")
    parser.add_argument("--out", default="playtests", help="Where playtest transcripts are written")
    parser.add_argument("--only", nargs="*", choices=sorted(SCENARIOS), help="Playtest scenarios to run")
    args = parser.parse_args()
    Path(args.data).mkdir(parents=True, exist_ok=True)
    settings = Settings(args.data, args.env_file)
    if args.command == "playtest":
        run_all(settings, args.data, args.out, args.only)
    else:
        serve(args.data, settings, args.port)


if __name__ == "__main__":
    main()
