# SciForge 开发与使用快捷命令
PY ?= .venv/bin/python
PIP ?= uv pip

.PHONY: help install install-full test lint format demo demo-web serve doctor clean

help:  ## 显示帮助
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## 安装基础依赖（editable）
	uv pip install -e ".[dev]"

install-full:  ## 安装全部可选依赖（pdf/web/fig）
	uv pip install -e ".[pdf,web,fig,dev]"

test:  ## 运行全部测试
	$(PY) -m pytest -q

lint:  ## ruff 检查
	$(PY) -m ruff check sciforge tests

format:  ## ruff 自动修复
	$(PY) -m ruff check --fix sciforge tests

demo:  ## 离线演示
	$(PY) -m sciforge.cli demo

demo-web:  ## 离线演示并启动 Web
	$(PY) -m sciforge.cli demo --web

serve:  ## 启动本地 Web 控制台
	$(PY) -m sciforge.cli serve

doctor:  ## 环境自检
	$(PY) -m sciforge.cli doctor

clean:  ## 清理缓存与演示产物
	rm -rf .ruff_cache .pytest_cache sciforge.egg-info sciforge/__pycache__ sciforge/*/__pycache__ tests/__pycache__
	rm -rf sciforge-demo
