from setuptools import setup, find_packages
from pathlib import Path

this_dir = Path(__file__).parent
long_description = (this_dir / "README.md").read_text(encoding="utf-8")

setup(
    name='tracyspot',
    version='1.0.37',
    description='Spot navigator',
    long_description=long_description,
    long_description_content_type="text/markdown",
    author='Sami Chaaban',
    author_email='sami.chaaban@gmail.com',
    url='https://github.com/sami-chaaban/tracy',
    packages=find_packages(),
    include_package_data=True,
    package_data={
        'tracy': ['icons/*.svg', 'icons/*.png', 'fonts/*.ttf', 'style.qss']
    },
    install_requires=[
        'numpy',
        'PyQt5',
        'matplotlib',
        'scipy',
        'pandas',
        'tifffile',
        'read-roi',
        'roifile',
        'openpyxl'
    ],
    entry_points={
        'gui_scripts': [
            'tracy = tracy.__main__:main',
        ],
    },
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Science/Research',
        'Topic :: Scientific/Engineering :: Visualization',

        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',

        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.10',
)
