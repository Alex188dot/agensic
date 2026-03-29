.PHONY: test release

test:
	./scripts/test $(ARGS)

release:
	./scripts/release.sh $(ARGS)
