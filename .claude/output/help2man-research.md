# Deep Research: help2man and CLI Help Text Extraction Tools

**Date:** 2026-01-17
**Purpose:** Building a tool to extract --help output from arbitrary CLI executables, discover subcommands, and generate man page documentation

---

## 1. help2man - Core Tool Analysis

### How help2man Works

help2man generates man pages by executing a program with `--help` and `--version` flags, then parsing the output according to a lexer. Given a program which produces reasonably standard `--help` and `--version` outputs, help2man can rearrange that output into something which resembles a manual page.

**Basic invocation:**
```bash
help2man <program> > program.1
```

### Parsing Mechanisms

help2man uses heuristics for identifying manual page sections:

- **Section detection:** A line consisting of `Options:` causes following text to appear in the OPTIONS section
- **Copyright detection:** Lines beginning with `Copyright` appear in the COPYRIGHT section
- **Custom sections:** `*Words*` (asterisk-wrapped) starts a new section
- **Subsections:** `Words:` (with colon) starts a new subsection

**Option parsing rules:**
- Option descriptions must be separated from options by **at least two spaces**
- Continued descriptions on subsequent lines must start at the **same column**

### Known Limitations

1. **Output destination issues:** Programs that don't output `--help` or `--version` to stdout require special handling
2. **Quality dependency:** Generated man page quality directly depends on input help text quality - garbage in, garbage out
3. **Limited pattern recognition:**
   - Ignores `positional arguments:` and `options:` (lowercase)
   - Only detects `Options:` (capitalized)
   - May incorrectly parse synopsis vs description
4. **Edge cases:**
   - Poor handling of multi-level subcommands
   - Struggles with dynamic command trees
   - Non-GNU help format support is limited

**Key insight:** help2man is not a substitute for proper man pages, but better than having none at all.

### References
- [GNU help2man Project](https://www.gnu.org/software/help2man/)
- [help2man Documentation](https://help2man.readthedocs.io/en/latest/index.html)
- [Debian Wiki: help2man](https://wiki.debian.org/ManualPage/help2man)
- [help2man man page](https://linux.die.net/man/1/help2man)

---

## 2. Python Libraries for Help Text Parsing and Man Page Generation

### argparse-manpage (Primary Recommendation)

**Repository:** [praiskup/argparse-manpage](https://github.com/praiskup/argparse-manpage)
**PyPI:** [argparse-manpage](https://pypi.org/project/argparse-manpage/)

Automatically builds man pages from Python's `ArgumentParser` object, avoiding duplicate documentation.

**Key features:**
- Leverages ArgumentParser's traversable tree structure
- Generates groff/troff output directly
- Integrates with `setup.py` build automation

**Installation & Usage:**
```bash
pip install argparse-manpage

# Command-line generation
argparse-manpage \
  --pyfile ./pythonfile.py \
  --function get_parser \
  --author "John Doe" \
  --author-email doe@example.com \
  --project-name myproject \
  --url https://example.com/myproject \
  > cool-manpage.1
```

**Setup.py integration:**
```python
from setuptools import setup
from build_manpages import build_manpages

setup(
    name='myproject',
    cmdclass={'build_manpages': build_manpages},
    ...
)
```

### click-man

**Repository:** [click-contrib/click-man](https://github.com/click-contrib/click-man)
**PyPI:** [click-man](https://pypi.org/project/click-man/)

Automates generation of man pages for Click-based CLI applications.

**Key features:**
- Generates one man page per command (from `console_scripts` in setup.py/setup.cfg/pyproject.toml)
- Provides its own CLI tool
- **Note:** distutils hook removed in v0.5.0 (Python 3.12+ compatibility)

**Usage:**
```bash
pip install click-man
click-man <installed-script-name>
```

### sphinx-click

**Repository:** [click-contrib/sphinx-click](https://github.com/click-contrib/sphinx-click)
**Documentation:** [sphinx-click.readthedocs.io](https://sphinx-click.readthedocs.io/)

Sphinx plugin for automatically documenting Click-based applications.

**Key features:**
- Automatic documentation extraction via Sphinx directive
- Renders help screen with deep links and styling
- Supports mocking imports (via `sphinx_click_mock_imports` or `autodoc_mock_imports`)
- Now includes support for `typer.main.TyperArgument` help attributes

**Usage in RST:**
```rst
.. click:: mymodule:cli
   :prog: my-program
   :nested: full
```

### sphinxcontrib.typer

**Repository:** [sphinx-contrib/typer](https://github.com/sphinx-contrib/typer)

Auto-generates docs for Typer and Click commands using Typer's rich console formatting.

**Key features:**
- Generates concise command documentation (text/HTML/SVG)
- Uses rich console formatting
- For heavy customization, sphinx-click may be more appropriate

### Core CLI Frameworks

**argparse (Standard Library):**
- Built into Python
- `add_subparsers()` method for subcommands
- Most documentation tools build on this

**Click:**
- External package with streamlined API
- Decorator-based interface
- Extensive ecosystem (click-man, sphinx-click)

**Typer:**
- Built on Click with type hints
- Modern Python 3.6+ syntax
- Rich terminal output

**Docopt:**
- Parses docstring-based interface definitions
- "Document first, parse second" philosophy
- Available as `docopt-ng` (maintained fork)

### References
- [argparse-manpage on GitHub](https://github.com/praiskup/argparse-manpage)
- [click-man on GitHub](https://github.com/click-contrib/click-man)
- [sphinx-click documentation](https://sphinx-click.readthedocs.io/)
- [sphinxcontrib.typer on GitHub](https://github.com/sphinx-contrib/typer)

---

## 3. Modern Alternatives to help2man

### Rust-based Tools

**rust-cli/man**
**Repository:** [rust-cli/man](https://github.com/rust-cli/man)

Generate structured man pages in Rust.

### Go-based Tools

**mango**
**Repository:** [muesli/mango](https://github.com/muesli/mango)

Man-page generator for Go flag, pflag, cobra, coral, and kong packages.

**mantis**
**Repository:** [tamerfrombk/mantis](https://github.com/tamerfrombk/mantis)

Golang man page generator.

**Cobra Framework**
**Website:** [cobra.dev](https://cobra.dev/)
**Repository:** [spf13/cobra](https://github.com/spf13/cobra)

Popular Go CLI framework with:
- Automatic man page generation
- Shell autocomplete generation
- LLM documentation generation
- TUI conversion support
- Subcommand-based architecture

### C++ Tools

**CLI11**
**Repository:** [CLIUtils/CLI11](https://github.com/CLIUtils/CLI11)
**Documentation:** [cliutils.github.io/CLI11](https://cliutils.github.io/CLI11/book/chapters/subcommands.html)

C++11+ command line parser with:
- Rich subcommand support
- Nested subcommands
- Option groups
- Multiple subcommands
- Optional fallthrough

### Python Alternatives

**cli2man** - Generate manpage and markdown from `--help` and `--version` (mentioned in search results but limited documentation found)

### References
- [rust-cli/man on GitHub](https://github.com/rust-cli/man)
- [mango on GitHub](https://github.com/muesli/mango)
- [Cobra Framework](https://cobra.dev/)
- [CLI11 on GitHub](https://github.com/CLIUtils/CLI11)

---

## 4. Structured Format Conversion Tools

### jc (JSON Convert)

**Repository:** [kellyjonbrazil/jc](https://github.com/kellyjonbrazil/jc)
**Website:** [kellyjonbrazil.github.io/jc](https://kellyjonbrazil.github.io/jc/)

CLI tool and Python library that converts output of popular command-line tools to JSON, YAML, or dictionaries.

**Key features:**
- Vast collection of parsers (each for specific commands)
- Can output JSON, YAML, or Python pickle
- `--raw` option for debugging
- Enables piping to `jq` for further processing

**Example usage:**
```bash
ls -la | jc --ls | jq
ip address | jc --ip-address
```

**Python library usage:**
```python
import jc

result = jc.parse('ls', ls_output)
```

**Limitations:**
- Parsers rely on text output not designed for parsing
- Would benefit from standardized `--json` flags across tools

### Markdown to Man Page Converters

**ronn**
**Repository:** [rtomayko/ronn](https://github.com/rtomayko/ronn)
**Man page:** [ronn(1)](https://rtomayko.github.io/ronn/ronn.1.html)

Converts Markdown files to man pages (roff format).

**Key features:**
- Rigid structure with Markdown syntax
- Extensions for man page features (definition lists, link notation)
- Generates both roff and HTML output
- Written in Ruby (dependencies: hpricot, rdiscount)

**Usage:**
```bash
ronn file.ronn
```

**pandoc**
**Website:** [pandoc.org](https://pandoc.org/)

Universal document converter (Haskell-based).

**Key features:**
- Converts between numerous markup formats
- Can generate groff man pages from Markdown
- More flexible than ronn
- Larger ecosystem and better maintenance

**Usage:**
```bash
pandoc --standalone --to man hello.1.md -o hello.1
```

**Note:** `--standalone` is required for proper man page structure.

**md2roff**
**Repository:** [bmoneill/md2roff](https://github.com/bmoneill/md2roff)

Markdown to roff (ms or manpage) compiler.

### References
- [jc on GitHub](https://github.com/kellyjonbrazil/jc)
- [ronn on GitHub](https://github.com/rtomayko/ronn)
- [Pandoc website](https://pandoc.org/)
- [Writing Man Pages with Pandoc](https://jeromebelleman.gitlab.io/posts/publishing/manpages/)
- [Authoring man pages in Markdown with Pandoc](https://eddieantonio.ca/blog/2015/12/18/authoring-manpages-in-markdown-with-pandoc/)

---

## 5. Subcommand Discovery and Hierarchical Command Trees

### Python Tools

**cli-command-parser**
**PyPI:** [cli-command-parser](https://pypi.org/project/cli-command-parser/)

Python package for command parsing with subcommand support.

**SubCommand**
**Documentation:** [subcommand.org](http://subcommand.org/)

Simple and concise SubCommand parser:
- Single file (<500 lines)
- Interface: 3 classes + decorators
- Supports nested subcommands

**argparse subparsers:**
```python
import argparse

parser = argparse.ArgumentParser()
subparsers = parser.add_subparsers()

# Add subcommand
parser_a = subparsers.add_parser('checkout')
parser_b = subparsers.add_parser('commit')
```

### Other Languages

**commandry (Standard ML)**
**Repository:** [PerplexSystems/commandry](https://github.com/PerplexSystems/commandry)

Command-line parser for Standard ML with:
- Nested subcommands
- Hierarchical command structures
- Auto-generated help

### Design Patterns

**Infinite nesting:**
- Subcommands use the same class as main app
- Enables infinitely nestable subcommands
- Sub-commands can have their own sub-commands

**Common structure:**
```
program command subcommand [options] [arguments]
```

Examples:
- `git commit -m "message"`
- `kubectl get pods --namespace=default`
- `docker container ls -a`

### References
- [argparse documentation](https://docs.python.org/3/library/argparse.html)
- [Multi-level argparse](https://chase-seibert.github.io/blog/2014/03/21/python-multilevel-argparse.html)
- [CLI11 Subcommands](https://cliutils.github.io/CLI11/book/chapters/subcommands.html)

---

## 6. CLI Best Practices and Design Guidelines

### Help Text Standards

**Command Line Interface Guidelines**
**Website:** [clig.dev](https://clig.dev/)

Modern CLI design principles.

**BetterCLI.org**
**Website:** [bettercli.org/design/cli-help-page](https://bettercli.org/design/cli-help-page/)

Guidelines for CLI help pages:
- One-liner description
- Examples and common use cases
- Show possible subcommands & options
- Guide users toward detailed documentation

### Documentation Requirements

**Fuchsia CLI Tool Help Requirements**
**Website:** [fuchsia.dev CLI help](https://fuchsia.dev/fuchsia-src/development/api/cli_help)

Requirements for production CLI tools:
- Make it possible to list available subcommands and arguments
- Don't require execution to show help
- Provide hierarchical documentation

### Subcommand Design

**Benefits:**
- Reduce complexity in complex tools
- Make related tools easier to discover
- Combine functionality into single command
- Enable logical grouping of features

### References
- [Command Line Interface Guidelines](https://clig.dev/)
- [BetterCLI.org](https://bettercli.org/design/cli-help-page/)
- [Fuchsia CLI Help Requirements](https://fuchsia.dev/fuchsia-src/development/api/cli_help)

---

## 7. Recommended Approach for Your Tool

Based on this research, here's a recommended architecture:

### For Python-based CLIs:
1. **argparse-manpage** - Best for pure argparse-based tools
2. **click-man** - Best for Click-based tools
3. **sphinx-click** - Best if you need HTML docs too

### For Arbitrary CLI Executables:

**Parsing Strategy:**
1. Execute `<command> --help` and capture output
2. Use help2man-style heuristics for section detection:
   - Look for `Options:`, `Commands:`, `Usage:`, etc.
   - Detect option syntax: `-x`, `--long-option`, `-x ARG`
   - Parse option descriptions (2+ space separator rule)
3. For structured output, consider implementing jc-style parsers
4. Store intermediate representation as JSON/YAML

**Subcommand Discovery:**
1. Parse help output for subcommand lists
2. Recursively execute `<command> <subcommand> --help`
3. Build hierarchical tree structure
4. Handle edge cases:
   - Commands that require arguments before showing help
   - Dynamic subcommands (e.g., git plugins)
   - Aliases and shortcuts

**Man Page Generation:**
1. Convert intermediate JSON/YAML to roff using:
   - Direct roff generation (study argparse-manpage source)
   - Markdown intermediate + pandoc conversion
   - Leverage existing templates from help2man

**Reference Implementations to Study:**
- [argparse-manpage source](https://github.com/praiskup/argparse-manpage) - roff generation
- [jc source](https://github.com/kellyjonbrazil/jc) - parsing patterns
- [help2man source](https://github.com/Distrotech/help2man) - heuristics

### Architecture Sketch:

```
┌─────────────────────┐
│  CLI Executor       │ → Execute --help, --version
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Help Text Parser   │ → Extract sections, options, descriptions
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Subcommand         │ → Discover and recurse
│  Discovery          │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Intermediate       │ → Store as JSON/YAML
│  Representation     │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Man Page Generator │ → Output roff format
└─────────────────────┘
```

---

## 8. Key Takeaways

1. **help2man is the standard** but has significant limitations with modern CLIs
2. **Python ecosystem is rich** with argparse-manpage and click-man as solid solutions
3. **Modern frameworks** (Cobra, Click, Typer) include built-in doc generation
4. **No universal solution exists** for arbitrary CLI executables
5. **jc demonstrates** feasible approach for parsing arbitrary text output
6. **Subcommand discovery** requires recursive execution and careful parsing
7. **Intermediate formats** (JSON/YAML) make pipeline more flexible
8. **Markdown → man page** converters (pandoc, ronn) provide alternative generation path

### Gap Analysis

**What exists:**
- Framework-specific generators (argparse-manpage, click-man, mango)
- Manual documentation tools (help2man, pandoc)
- Structured output converters (jc)

**What's missing:**
- **Universal CLI help parser** that handles arbitrary formats
- **Automatic subcommand discovery** with recursive documentation
- **Standardized help text format** across tools (though `--json` flag would help)
- **Library for parsing help text patterns** agnostic to framework

**Your project fills this gap!**

---

## Sources

### help2man
- [help2man - GNU Project](https://www.gnu.org/software/help2man/)
- [help2man documentation](https://help2man.readthedocs.io/en/latest/index.html)
- [ManualPage/help2man - Debian Wiki](https://wiki.debian.org/ManualPage/help2man)
- [help2man(1) man page](https://linux.die.net/man/1/help2man)
- [GitHub - Distrotech/help2man](https://github.com/Distrotech/help2man)

### Python Libraries
- [GitHub - praiskup/argparse-manpage](https://github.com/praiskup/argparse-manpage)
- [argparse-manpage on PyPI](https://pypi.org/project/argparse-manpage/)
- [GitHub - click-contrib/click-man](https://github.com/click-contrib/click-man)
- [click-man on PyPI](https://pypi.org/project/click-man/)
- [sphinx-click documentation](https://sphinx-click.readthedocs.io/)
- [GitHub - click-contrib/sphinx-click](https://github.com/click-contrib/sphinx-click)
- [GitHub - sphinx-contrib/typer](https://github.com/sphinx-contrib/typer)
- [argparse documentation](https://docs.python.org/3/library/argparse.html)

### Modern Alternatives
- [GitHub - rust-cli/man](https://github.com/rust-cli/man)
- [GitHub - muesli/mango](https://github.com/muesli/mango)
- [GitHub - tamerfrombk/mantis](https://github.com/tamerfrombk/mantis)
- [Cobra Framework](https://cobra.dev/)
- [GitHub - spf13/cobra](https://github.com/spf13/cobra)
- [GitHub - CLIUtils/CLI11](https://github.com/CLIUtils/CLI11)
- [CLI11 Documentation](https://cliutils.github.io/CLI11/book/chapters/subcommands.html)

### Structured Format Conversion
- [GitHub - kellyjonbrazil/jc](https://github.com/kellyjonbrazil/jc)
- [jc website](https://kellyjonbrazil.github.io/jc/)
- [GitHub - rtomayko/ronn](https://github.com/rtomayko/ronn)
- [ronn(1) man page](https://rtomayko.github.io/ronn/ronn.1.html)
- [Pandoc](https://pandoc.org/)
- [Writing Man Pages with Pandoc](https://jeromebelleman.gitlab.io/posts/publishing/manpages/)
- [Authoring man pages in Markdown with Pandoc](https://eddieantonio.ca/blog/2015/12/18/authoring-manpages-in-markdown-with-pandoc/)
- [GitHub - bmoneill/md2roff](https://github.com/bmoneill/md2roff)

### Subcommand Discovery
- [cli-command-parser on PyPI](https://pypi.org/project/cli-command-parser/)
- [SubCommand documentation](http://subcommand.org/)
- [GitHub - PerplexSystems/commandry](https://github.com/PerplexSystems/commandry)
- [Multi-level argparse in Python](https://chase-seibert.github.io/blog/2014/03/21/python-multilevel-argparse.html)

### Best Practices
- [Command Line Interface Guidelines](https://clig.dev/)
- [BetterCLI.org](https://bettercli.org/design/cli-help-page/)
- [Fuchsia CLI Tool Help Requirements](https://fuchsia.dev/fuchsia-src/development/api/cli_help)
