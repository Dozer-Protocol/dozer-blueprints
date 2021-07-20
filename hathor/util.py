# Copyright 2021 Hathor Labs
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import math
import warnings
from collections import OrderedDict
from enum import Enum
from functools import partial, wraps
from typing import Any, Callable, Deque, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, TypeVar, cast

import numpy as np
from numpy.random import Generator as Rng
from structlog import get_logger
from twisted.internet.interfaces import IReactorCore
from twisted.python.threadable import isInIOThread

from hathor.conf import HathorSettings

logger = get_logger()
settings = HathorSettings()


T = TypeVar('T')


def practically_equal(a: Dict[Any, Any], b: Dict[Any, Any]) -> bool:
    """ Compare two defaultdict. It is used because a simple access have
    side effects in defaultdict.

    >>> from collections import defaultdict
    >>> a = defaultdict(list)
    >>> b = defaultdict(list)
    >>> a == b
    True
    >>> a[0]
    []
    >>> a == b
    False
    >>> practically_equal(a, b)
    True
    """
    for k, v in a.items():
        if v != b[k]:
            return False
    for k, v in b.items():
        if v != a[k]:
            return False
    return True


def deprecated(msg: str) -> Callable[..., Any]:
    """Use to indicate that a function or method has been deprecated."""
    warnings.simplefilter('default', DeprecationWarning)

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # warnings.warn('{} is deprecated. {}'.format(func.__name__, msg),
            #               category=DeprecationWarning, stacklevel=2)
            return func(*args, **kwargs)

        wrapper.__deprecated = func  # type: ignore
        return wrapper

    return decorator


def skip_warning(func: Callable[..., Any]) -> Callable[..., Any]:
    f = cast(Callable[..., Any], getattr(func, '__deprecated', func))
    if hasattr(func, '__self__') and not hasattr(f, '__self__'):
        return partial(f, getattr(func, '__self__'))
    else:
        return f


class ReactorThread(Enum):
    MAIN_THREAD = 'MAIN_THREAD'
    NOT_MAIN_THREAD = 'NOT_MAIN_THREAD'
    NOT_RUNNING = 'NOT_RUNNING'

    @classmethod
    def get_current_thread(cls, reactor: IReactorCore) -> 'ReactorThread':
        """ Returns if the code is being run on the reactor thread, if it's running already.
        """
        if hasattr(reactor, 'running'):
            if reactor.running:
                return cls.MAIN_THREAD if isInIOThread() else cls.NOT_MAIN_THREAD
            else:
                # if reactor is not running yet, there's no threading
                return cls.NOT_RUNNING
        else:
            # on tests, we use Clock instead of a real Reactor, so there's
            # no threading. We consider that the reactor is running
            return cls.MAIN_THREAD


def abbrev(data: bytes, max_len: int = 256, gap: bytes = b' [...] ') -> bytes:
    """ Abbreviates data, mostly for less verbose but still useful logging.

    Examples:

    >>> abbrev(b'foobar barbaz', 9, b'...')
    b'foo...baz'

    >>> abbrev(b'foobar barbaz', 9, b'..')
    b'foob..baz'

    >>> abbrev(b'foobar barbaz', 9, b'.')
    b'foob.rbaz'
    """
    if len(data) <= max_len:
        return data
    trim_len = max_len - len(gap)
    assert trim_len > 1, 'max_len and gap should be such that it leaves room for 1 byte on each side'
    tail_len = trim_len // 2
    head_len = trim_len - tail_len
    return data[:head_len] + gap + data[-tail_len:]


def ichunks(array: bytes, chunk_size: int) -> Iterator[bytes]:
    """ Split and yield chunks of the given size.
    """
    from itertools import islice, repeat, takewhile
    idata = iter(array)
    return takewhile(bool, (bytes(islice(idata, chunk_size)) for _ in repeat(None)))


def iwindows(iterable: Iterable[T], window_size: int) -> Iterator[Tuple[T, ...]]:
    """ Adapt iterator to yield windows of the given size.

    window_size must be greater than 0

    Example:

    >>> list(iwindows([1, 2, 3, 4], 2))
    [(1, 2), (2, 3), (3, 4)]

    >>> list(iwindows([1, 2, 3, 4], 3))
    [(1, 2, 3), (2, 3, 4)]

    >>> list(iwindows([1, 2, 3, 4], 1))
    [(1,), (2,), (3,), (4,)]
    """
    from collections import deque
    it = iter(iterable)
    assert window_size > 0
    res_item: Deque[T] = deque()
    while len(res_item) < window_size:
        res_item.append(next(it))
    yield tuple(res_item)
    for item in it:
        res_item.popleft()
        res_item.append(item)
        yield tuple(res_item)


class classproperty:
    """ This function is used to make a property that can be accessed from the class. Only getter is supported.

    See: https://stackoverflow.com/a/5192374/947511
    """

    def __init__(self, f):
        self.f = f

    def __get__(self, obj, owner):
        return self.f(owner)


class MaxSizeOrderedDict(OrderedDict):
    """ And OrderedDict that has a maximum size, if new elements are added, the oldest elements are silently deleted.

    Examples:

    >>> foo = MaxSizeOrderedDict(max=5)
    >>> foo[1] = 'a'
    >>> foo[2] = 'b'
    >>> foo[3] = 'c'
    >>> foo[4] = 'd'
    >>> foo[5] = 'e'
    >>> foo
    MaxSizeOrderedDict([(1, 'a'), (2, 'b'), (3, 'c'), (4, 'd'), (5, 'e')])
    >>> foo[6] = 'f'
    >>> foo
    MaxSizeOrderedDict([(2, 'b'), (3, 'c'), (4, 'd'), (5, 'e'), (6, 'f')])
    >>> foo[7] = 'g'
    >>> foo
    MaxSizeOrderedDict([(3, 'c'), (4, 'd'), (5, 'e'), (6, 'f'), (7, 'g')])
    """
    # Kindly stolen from: https://stackoverflow.com/a/49274421/947511
    def __init__(self, *args, max=0, **kwargs):
        self._max = max
        super().__init__(*args, **kwargs)

    def __setitem__(self, key, value):
        OrderedDict.__setitem__(self, key, value)
        if self._max > 0:
            if len(self) > self._max:
                self.popitem(False)


def json_loadb(raw: bytes) -> Dict:
    """Compact loading raw as UTF-8 encoded bytes to a Python object."""
    import json

    # XXX: from Python3.6 onwards, json.loads can take bytes
    #      See: https://docs.python.org/3/library/json.html#json.loads
    return json.loads(raw)


def json_dumpb(obj: object) -> bytes:
    """Compact formating obj as JSON to UTF-8 encoded bytes."""
    import json
    return json.dumps(obj, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def api_catch_exceptions(func: Callable[..., bytes]) -> Callable[..., bytes]:
    """Decorator to catch `hathor.exception.HathorError` and convert to API return type.

    Useful for annotating API methods and reduce error handling boilerplate.
    """
    from autobahn.twisted.websocket import WebSocketAdapterProtocol
    from twisted.web.http import Request
    from twisted.web.resource import Resource

    from hathor.exception import HathorError

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except HathorError as e:
            self = args[0] if len(args) > 0 else None
            if isinstance(self, Resource):
                request = cast(Optional[Request], args[1] if len(args) > 1 else None)
                if request is not None:
                    request.setResponseCode(getattr(e, 'status_code', 500))
                return json_dumpb({'error': str(e)})
            elif isinstance(self, WebSocketAdapterProtocol):
                self.sendClose(reason=json_dumpb({'error': str(e)}))
            else:
                logger.error('could not handle error', args=args, error=e)
                raise  # reraise because we don't know how to handle this
    return wrapper


class LogDuration(float):
    def __str__(x):
        if x >= 1:
            return f'~{math.trunc(x)}s'
        elif x >= 0.001:
            return f'~{math.trunc(x * 1000)}ms'
        else:
            return '<1ms'
    __repr__ = __str__


_T = TypeVar("_T")


# borrowed from: https://github.com/facebook/pyre-check/blob/master/pyre_extensions/__init__.py
def not_none(optional: Optional[_T], message: str = 'Unexpected `None`') -> _T:
    """Convert an optional to its value. Raises an `AssertionError` if the
    value is `None`"""
    if optional is None:
        raise AssertionError(message)
    return optional


def random_choices(rng: Rng, options: Sequence[_T], size: int) -> List[_T]:
    """Choose `size` randomly selected elements from `options` sequence."""
    # XXX: explicitly convert to numpy array to prevent numpy from converting a NamedTuple (i.e.) to a numpy array too
    _options = np.empty(len(options), dtype=object)
    _options[:] = options
    return list(rng.choice(_options, size, replace=False))


def random_choice(rng: Rng, options: Sequence[_T]) -> _T:
    """Choose a single randomly selected element from `options` sequence."""
    # XXX: explicitly convert to numpy array to prevent numpy from converting a NamedTuple (i.e.) to a numpy array too
    _options = np.empty(len(options), dtype=object)
    _options[:] = options
    return rng.choice(_options)
