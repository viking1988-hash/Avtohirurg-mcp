import importlib.util
import pathlib
import sys
root=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('jarvis_return_worker',root/'__init__.py',submodule_search_locations=[str(root)])
module=importlib.util.module_from_spec(spec)
sys.modules['jarvis_return_worker']=module
spec.loader.exec_module(module)
