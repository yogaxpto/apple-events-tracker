# Dev image: install all dependencies straight into the image's system Python —
# no virtualenv. uv targets /usr/local (UV_PROJECT_ENVIRONMENT), the same prefix the
# running container uses, so the container is ready without a heavy post-create sync
# and nothing depends on a writable .venv at runtime.
#
# The container runs as the non-root user `vscode` (see devcontainer.json -> remoteUser):
# Claude Code's VS Code extension launches with --dangerously-skip-permissions inside a
# trusted devcontainer, and Claude refuses to run that as root. We still want the no-venv
# / system-env approach, so `vscode` is given ownership of /usr/local — editable installs
# and `uv run` then work without root.
#
# Pin to 3.14: the project targets py314 (requires-python, ruff, mypy) and all locked
# deps (incl. lxml 6.1.1) ship cp314 wheels, so the slim image needs no C toolchain.
ARG PYTHON_VERSION=3.14
FROM python:${PYTHON_VERSION}-slim

ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=$USER_UID

ENV UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/root/.cache/uv \
    PIP_CACHE_DIR=/root/.cache/pip

# make drives the project's Makefile workflow; git is needed by both the tracker and
# Claude Code. Keep this layer lean — runtime deps are pure/manylinux wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends make git \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install uv

# Create the non-root user the container runs as, with a home dir for Claude Code's
# config/auth (persisted via a named volume in .devcontainer/docker-compose.yml).
RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m -s /bin/bash $USERNAME \
    && mkdir -p /home/$USERNAME/.claude \
    && chown -R $USER_UID:$USER_GID /home/$USERNAME

WORKDIR /app

# Install the locked dependencies first; this layer is cached unless pyproject.toml or
# uv.lock changes. The project itself is installed editable at container-create time
# (`make post-create` -> `uv sync`) against the bind-mounted source.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# Hand the system Python env to the non-root user so editable installs and `uv run`
# work without root (this is what replaces the need for a workspace-local .venv).
RUN chown -R $USER_UID:$USER_GID /usr/local
