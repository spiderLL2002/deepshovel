.PHONY: public-install public-update public-sync-update public-status public-open public-restart public-start public-stop public-logs public-verify

public-install:
	@bash scripts/install_public_service.sh install

public-update:
	@bash scripts/install_public_service.sh update

public-status:
	@bash scripts/public_status.sh

public-open:
	@install_root="$$HOME/Library/Application Support/$$(python3 scripts/public_manifest.py get install_directory)"; \
	"$$install_root/app/deploy/runtime/service-control.sh" open

public-restart:
	@install_root="$$HOME/Library/Application Support/$$(python3 scripts/public_manifest.py get install_directory)"; \
	"$$install_root/app/deploy/runtime/service-control.sh" restart

public-start:
	@install_root="$$HOME/Library/Application Support/$$(python3 scripts/public_manifest.py get install_directory)"; \
	"$$install_root/app/deploy/runtime/service-control.sh" start

public-stop:
	@install_root="$$HOME/Library/Application Support/$$(python3 scripts/public_manifest.py get install_directory)"; \
	"$$install_root/app/deploy/runtime/service-control.sh" stop

public-logs:
	@install_root="$$HOME/Library/Application Support/$$(python3 scripts/public_manifest.py get install_directory)"; \
	"$$install_root/app/deploy/runtime/service-control.sh" logs

public-verify:
	@bash scripts/verify_public_bundle.sh

# Preview by default. Stop all project agents before explicit irreversible reset.
.PHONY: runtime-clean runtime-clean-apply
runtime-clean:
	@python3 scripts/clean_runtime.py

runtime-clean-apply:
	@python3 scripts/clean_runtime.py --apply --confirm-project-id "$(CONFIRM)"

# Install only the result synchronizer; leave the active Web/Compiler untouched.
public-sync-update:
	@python3 scripts/install_compile_sync.py
