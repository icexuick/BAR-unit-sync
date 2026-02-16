# Contributing to BAR Unit Sync

Thank you for your interest in contributing to the Beyond All Reason Unit Sync project! 🎉

## How to Contribute

### Reporting Bugs

If you find a bug, please open an issue with:
- A clear description of the problem
- Steps to reproduce
- Expected vs actual behavior
- Your Python version and operating system
- Any relevant log output

### Suggesting Enhancements

We welcome suggestions! Please open an issue with:
- A clear description of the enhancement
- Why it would be useful
- Any implementation ideas you have

### Pull Requests

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Test your changes thoroughly
5. Commit with clear messages (`git commit -m 'Add amazing feature'`)
6. Push to your branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

### Development Setup

```bash
# Clone your fork
git clone https://github.com/your-username/bar-unit-sync.git
cd bar-unit-sync

# Install in development mode
pip install -r requirements.txt

# Run tests
python test_sync.py
```

### Code Style

- Follow PEP 8 style guidelines
- Use meaningful variable names
- Add docstrings to functions and classes
- Comment complex logic

### Testing

Before submitting a PR:
- Run `python test_sync.py` to verify parsing works
- Test with `--dry-run` on real data
- Ensure no API tokens are committed

## Questions?

Feel free to open an issue for any questions about contributing!
