import pytest
from backend.analyzer import validate_github_url

def test_valid_github_urls():
    url, owner, repo = validate_github_url("https://github.com/fastapi/fastapi")
    assert url == "https://github.com/fastapi/fastapi"
    assert owner == "fastapi"
    assert repo == "fastapi"

    url, owner, repo = validate_github_url("https://github.com/Jyotiraditya21-bug/InSightForge.git")
    assert url == "https://github.com/Jyotiraditya21-bug/InSightForge"
    assert owner == "Jyotiraditya21-bug"
    assert repo == "InSightForge"

    url, owner, repo = validate_github_url("https://www.github.com/user/my-repo/")
    assert url == "https://github.com/user/my-repo"
    assert owner == "user"
    assert repo == "my-repo"


def test_invalid_urls():
    # Non-https
    with pytest.raises(ValueError, match="Only secure HTTPS"):
        validate_github_url("http://github.com/owner/repo")

    # Non-github domain
    with pytest.raises(ValueError, match="hosted directly on github.com"):
        validate_github_url("https://gitlab.com/owner/repo")

    with pytest.raises(ValueError, match="hosted directly on github.com"):
        validate_github_url("https://evil-site.com/github.com/owner/repo")

    # Embedded credentials
    with pytest.raises(ValueError, match="Embedded credentials"):
        validate_github_url("https://user:password@github.com/owner/repo")

    # Query params and fragments
    with pytest.raises(ValueError, match="query parameters or fragments"):
        validate_github_url("https://github.com/owner/repo?ref=main")

    # Flag injection attempt
    with pytest.raises(ValueError, match="starting with '-'"):
        validate_github_url("https://github.com/owner/-exploit")

    # Invalid path depth
    with pytest.raises(ValueError, match="must point directly to a GitHub repository"):
        validate_github_url("https://github.com/owner")

    with pytest.raises(ValueError, match="must point directly to a GitHub repository"):
        validate_github_url("https://github.com/owner/repo/pull/1")
