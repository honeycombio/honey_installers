# Building the installer executables

You must have pyinstaller installed

	$ pip install -r requirements.txt

Then run it to create bundled executables

	$ pyinstaller mongo_installer/mongo_installer.spec
	$ pyinstaller mysql_installer/mysql_installer.spec
	$ pyinstaller nginx_installer/nginx_installer.spec

	$ ls dist/
	mongo_installer* mysql_installer* nginx_installer*
