"""Placeholder CLI."""

import typer

app = typer.Typer()


@app.command()
def version():
    print("ok")
