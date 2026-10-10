# Publishing AutoScripts on PyPI

The package has the distribution name `autoscripts-local` (import name: `autoscripts`). **Check the name on PyPI before uploading**; no name is guaranteed available. If already taken, change `[project].name` in `pyproject.toml`, references in this document and GitHub workflow, then rebuild. You also need to increment `version` for each published release and keep `autoscripts.__version__` in sync.

## Local release (Windows PowerShell or Linux/macOS)

From the project root:

```bash
python -m pip install --upgrade build twine
python -m unittest discover -s tests -v
python -m build
python -m twine check dist/*
```

On PowerShell, wildcard expansion for external commands may vary; if needed run:

```powershell
python -m twine check (Get-ChildItem dist/* | ForEach-Object FullName)
```

Build artifacts are written in `dist/`: a `.tar.gz` source distribution and a universal `.whl` wheel. The import package needs no third-party runtime dependencies. The GUI requires a Python installation providing Tkinter and a desktop graphical display.

## TestPyPI first (recommended)

1. Create a TestPyPI account at https://test.pypi.org/ and obtain a TestPyPI API token.
2. Upload with `python -m twine upload --repository testpypi dist/*` (on PowerShell pass expanded files if needed).
3. Use username `__token__` and the token as password when prompted. Never commit a token to the repository.
4. Install from TestPyPI with `python -m pip install --index-url https://test.pypi.org/simple/ --no-deps autoscripts-local` in a clean virtual environment. Replace the distribution name if you changed it.

## Publish to main PyPI manually

1. Create an account at https://pypi.org/ and obtain an API token.
2. `python -m twine upload dist/*` (PowerShell: expand files as above).
3. Enter username `__token__` and the API token as the password.
4. Check the project page and `pip install autoscripts-local` from a new virtual environment.

## Publish with GitHub Actions instead of an API token

The repository includes `.github/workflows/publish.yml`. You must first create a GitHub repository containing these files and set up PyPI *Trusted Publishing* with your GitHub owner, repository, workflow filename `publish.yml`, and environment `pypi`. For a new project, configure the pending publisher under PyPI's "Publishing" page. Once registered, publish a GitHub Release to trigger the workflow. This workflow does not require a stored PyPI API token.

**Before publishing:** fill in your project-specific metadata, verify the license and README, run tests, reserve/check the distribution name, and use a new version for each upload. Publishing requires your own PyPI account; this ZIP has **not** been uploaded on your behalf.
