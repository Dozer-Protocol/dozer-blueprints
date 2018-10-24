
.PHONY: all
all: tests pep8

.PHONY: tests
tests:
	nosetests --with-coverage --cover-package=hathor --cover-html --cover-min-percentage=80 --cover-erase

.PHONY: pep8
pep8:
	flake8 hathor/ tests/ tools/ *.py



