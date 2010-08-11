## Simple file you can run with ipython if you want to poke around the API ##
import sys
sys.path.append("../../src")
from pulp.api.consumer import ConsumerApi
from pulp.api.package import PackageApi
from pulp.api.repo import RepoApi
from pulp.api.user import UserApi

from pulp.db.model import Package
from pulp.db.model import Consumer
from pulp.db.model import Repo

capi = ConsumerApi()
papi = PackageApi()
rapi = RepoApi()
uapi = UserApi()
