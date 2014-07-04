from setuptools import setup


setup(
    name='metaflask-api',
    author='Armin Ronacher',
    author_email='armin.ronacher@active-4.com',
    url='http://github.com/pocoo/metaflask-api',
    py_modules=['metaflaskapi', 'libmetaflask'],
    install_requires=[
        'Flask',
        'requests',
    ],
    description='Helps managing the metaflask repo.',
    classifiers=[
        'DO NOT UPLOAD',
    ],
)
