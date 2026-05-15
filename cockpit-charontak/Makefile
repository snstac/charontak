PREFIX ?= /usr/local
DESTDIR ?=
PKGDIR = $(DESTDIR)$(PREFIX)/share/cockpit/charontak

.PHONY: install
install:
	install -d $(PKGDIR)
	install -m644 manifest.json index.html charontak.js $(PKGDIR)/
