import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run VANSH, the robo coach for cricket nets (vansh.best).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--proxy",
        action="store_true",
        help="trust X-Forwarded-* from a reverse proxy on this host or its Docker bridge (Caddy on vansh.best)",
    )
    args = parser.parse_args()
    options = {}
    if args.proxy:
        options = {"proxy_headers": True, "forwarded_allow_ips": "127.0.0.1,172.16.0.0/12"}
    uvicorn.run("crickcoach.app:app", host=args.host, port=args.port, **options)


if __name__ == "__main__":
    main()
