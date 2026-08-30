.PHONY: help setup migrate seed run test lint fmt check clean

VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help:  ## このヘルプを表示する
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup:  ## 仮想環境を作り、開発用依存をインストールする
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements/dev.txt
	@test -f .env || cp .env.example .env

migrate:  ## マイグレーションを適用する
	$(PY) manage.py migrate

seed:  ## 計数・目標管理の体験用データを投入する
	$(PY) manage.py seed_performance

run:  ## 開発サーバーを起動する
	$(PY) manage.py runserver

test:  ## テストを実行する
	$(PY) manage.py test apps --settings=config.settings.test

lint:  ## 静的チェック
	$(VENV)/bin/ruff check .

fmt:  ## 自動整形
	$(VENV)/bin/ruff format .
	$(VENV)/bin/ruff check --fix .

check:  ## Django のシステムチェック（本番設定含む）
	$(PY) manage.py check
	$(PY) manage.py makemigrations --check --dry-run

clean:  ## 生成物を削除する
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf var/staticfiles .ruff_cache .coverage
