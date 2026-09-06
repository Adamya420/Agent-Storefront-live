.PHONY: install keys catalog mandate migrate test test-unit test-integration run
install:       ; pip install -r requirements.txt
keys:          ; python data/generate_keys.py
catalog:       ; python data/generate_catalog.py --seed 42 --count 150
mandate:       ; python data/issue_mandate.py --category running_shoes --max-price 500000 --min-return 21 --max-delivery 3 --tolerate return,bundle --merchant "merchant://acg-sports" --ttl 15
migrate:       ; alembic upgrade head
test-unit:     ; pytest backend/tests/unit backend/gateway/tests -v
test-integration: ; pytest backend/tests/integration -v -m integration
test:          ; pytest backend/tests/unit backend/gateway/tests -v
run:           ; uvicorn backend.api.main:app --reload
