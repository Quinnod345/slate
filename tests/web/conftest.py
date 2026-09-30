"""The web engine's pytest folder: real renders are opt-in (SLATE_TEST_RENDER=1), so the default run stays fast."""


def pytest_configure(config):
    config.addinivalue_line("markers", "render: a real render with headless Chrome and ffmpeg (opt in: SLATE_TEST_RENDER=1)")
