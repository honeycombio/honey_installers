from setuptools import setup

setup(
    name='mysql_installer',
    version='0.1',
    py_modules=['mysql_installer'],
    install_requires=[
        'click==6.6',
        'requests==2.11.1',
        'semver==2.6.0'
    ],
    entry_points='''
        [console_scripts]
        mysql_installer=mysql_installer:start
    ''',
)
