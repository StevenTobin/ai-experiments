.PHONY: collect report serve dashboard dashboard-down clean help

PYTHON ?= python3
CLI = $(PYTHON) cli.py
EXPORTER_PID_FILE = data/.exporter.pid

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

collect: ## Clone/fetch repos and collect all data from git history
	$(CLI) collect

report: ## Print a DORA metrics summary to the terminal
	$(CLI) report

serve: ## Start the Prometheus metrics exporter on :9090 (foreground)
	$(CLI) serve

dashboard: ## Start Prometheus + Grafana + exporter
	@# Stop any existing exporter
	@if [ -f $(EXPORTER_PID_FILE) ]; then \
		kill $$(cat $(EXPORTER_PID_FILE)) 2>/dev/null || true; \
		rm -f $(EXPORTER_PID_FILE); \
	fi
	docker compose -f dashboard/docker-compose.yml up -d
	@mkdir -p data
	$(CLI) serve &> data/exporter.log & echo $$! > $(EXPORTER_PID_FILE)
	@sleep 2
	@echo ""
	@echo "  Grafana:  http://localhost:3000  (admin/admin)"
	@echo "  Exporter: http://localhost:9090/metrics"
	@echo "  Exporter PID: $$(cat $(EXPORTER_PID_FILE))"
	@echo "  Exporter log: data/exporter.log"
	@echo ""
	@echo "  Run 'make dashboard-down' to stop everything."

dashboard-down: ## Stop the Docker Compose stack and exporter
	@if [ -f $(EXPORTER_PID_FILE) ]; then \
		kill $$(cat $(EXPORTER_PID_FILE)) 2>/dev/null || true; \
		rm -f $(EXPORTER_PID_FILE); \
		echo "Exporter stopped."; \
	fi
	docker compose -f dashboard/docker-compose.yml down

clean: ## Remove cached data (clones + sqlite)
	rm -rf data/
