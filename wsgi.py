"""Entry point for waitress (shop-hub.service) and the flask CLI."""

from app import create_app

app = create_app()
