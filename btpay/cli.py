#
# BTPay CLI entry point
#
# Installed via: pip install btpay
# Usage: btpay [--port PORT] [--host HOST] [--demo]
#
import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        prog='btpay',
        description='BTPay - Self-hosted Bitcoin payment processor',
    )
    parser.add_argument('--port', type=int, default=int(os.environ.get('PORT', 5000)),
                        help='Port to listen on (default: 5000)')
    parser.add_argument('--host', default='127.0.0.1',
                        help='Host to bind to (default: 127.0.0.1)')
    parser.add_argument('--demo', action='store_true',
                        help='Run in demo mode with sample data')
    parser.add_argument('--data-dir', default=None,
                        help='Data directory (default: ./data)')
    args = parser.parse_args()

    if args.demo:
        os.environ['BTPAY_DEMO'] = '1'
    if args.data_dir:
        os.environ['BTPAY_DATA_DIR'] = args.data_dir

    # Import here so env vars are set before app factory runs
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app import create_app

    app = create_app()
    app.run(debug=True, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
