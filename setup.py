from setuptools import setup, find_packages

setup(
    packages=find_packages(include=["src", "src.*", "infra", "infra.*"]),
    package_data={
        "infra": ["*.json"],
    },
)
