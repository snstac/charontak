#
# Makefile (mirrors snstac/adsbcot + pytak packaging targets).
#

REPO_NAME := charontak
PKG_NAME := charontak

SHELL := /bin/bash
PYTHON := $(shell command -v python3 2>/dev/null || command -v python)

.PHONY: install editable uninstall pep257 pylint flake8 pytest clean faux_latest package deb_dist deb_custom bdist_deb install_test_requirements test test_cov

prepare:
	mkdir -p build/

editable:
	$(PYTHON) -m pip install -e ".[dev]"

install_test_requirements:
	$(PYTHON) -m pip install -r requirements_test.txt

install:
	$(PYTHON) -m pip install .

uninstall:
	$(PYTHON) -m pip uninstall -y $(PKG_NAME)

clean:
	@echo "Cleaning..."
	rm -rf *.egg-info dist build *.deb deb_dist faux_latest
	find . -name '*.pyc' -exec rm -f {} \; 2>/dev/null || true

pytest:
	$(PYTHON) -m pytest

test: editable install_test_requirements pytest

test_cov:
	$(PYTHON) -m pytest --cov=$(PKG_NAME) --cov-report term-missing

deb_dist:
	rm -rf deb_dist/
	$(PYTHON) setup.py --command-packages=stdeb.command sdist_dsc

deb_custom:
	cp debian/$(REPO_NAME).default $(wildcard deb_dist/*/debian)/$(REPO_NAME).default
	cp debian/$(REPO_NAME).postinst $(wildcard deb_dist/*/debian)/$(REPO_NAME).postinst
	cp debian/$(REPO_NAME).service $(wildcard deb_dist/*/debian)/$(REPO_NAME).service
	cp debian/$(REPO_NAME).install $(wildcard deb_dist/*/debian)/$(REPO_NAME).install
	cat debian/rules_fragment >> $$(echo deb_dist/*/debian/rules)

bdist_deb: deb_dist deb_custom
	cd deb_dist/$(REPO_NAME)-*/ && dpkg-buildpackage -rfakeroot -uc -us

faux_latest:
	mkdir -p faux_latest
	set -e; \
	deb=$$(ls deb_dist/$(REPO_NAME)_*-1_all.deb 2>/dev/null | head -n1); \
	if [ -z "$$deb" ]; then deb=$$(ls deb_dist/python3-$(REPO_NAME)_*-1_all.deb 2>/dev/null | head -n1); fi; \
	if [ -z "$$deb" ]; then echo "No .deb found under deb_dist/"; exit 1; fi; \
	cp "$$deb" faux_latest/$(REPO_NAME)_latest_all.deb; \
	cp "$$deb" faux_latest/python3-$(REPO_NAME)_latest_all.deb

package: bdist_deb faux_latest
