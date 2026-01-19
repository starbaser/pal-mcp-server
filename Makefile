.PHONY: build test clean

build:
	@echo "Building clarkd..."
	cd cmd/clarkd && go build -o ../../build/clarkd .
	@echo "Build complete: build/clarkd"

test:
	@echo "Running tests..."
	go test -tags fts5 ./pkg/container/... -v

clean:
	@echo "Cleaning build artifacts..."
	rm -rf build/
	@echo "Clean complete"
