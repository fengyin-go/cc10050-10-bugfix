.PHONY: install backend frontend test

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm ci

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

test:
	backend/.venv/bin/python -I tests/test_receipt_attribution.py
	cd frontend && npm run typecheck
