# Match Debian/RPM packages (/usr/share/cockpit/charontak). Override: make install PREFIX=/usr/local
PREFIX ?= /usr
DESTDIR ?=
PKGDIR = $(DESTDIR)$(PREFIX)/share/cockpit/charontak

.PHONY: install
install:
	install -d $(PKGDIR)
	install -m644 manifest.json index.html charontak.js $(PKGDIR)/
