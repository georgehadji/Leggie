from setuptools import find_packages, setup

setup(
    name="leggie",
    version="0.1.0",
    packages=find_packages(exclude=["config", "config.*", "tests", "tests.*"]),
)
