# Using sqs-consumer for the queue consumer

This branch follows "RAI-162 Save documents with many images without timeout"
([DEFRA/rpa-ai-guidance-hub-api#22](https://github.com/DEFRA/rpa-ai-guidance-hub-api/pull/22)). That
PR has its own small consumer. This branch replaces it with
[sfitz42/sqs-consumer](https://github.com/sfitz42/sqs-consumer), as like for like as possible, to
find out what using the library would take.

**In short:** it works, and it passed everything our own consumer passed. But it saves very little
code, because the API needs several things the library doesn't offer, and it isn't ready to depend
on yet: it has no licence file, and no release to pin. Our recommendation is to merge the PR with
its own consumer, ask for the changes listed below, and swap later. The swap is small, because the
conversion handler doesn't change.

## How it's installed

`pyproject.toml` pins version 0.2.0, the repo's only commit, as GitHub's tarball of that commit:

```toml
[tool.uv.sources]
sqs-consumer = { url = "https://github.com/sfitz42/sqs-consumer/archive/755f135071b33e3107729166176915e35b171dea.tar.gz" }
```

`uv.lock` records the tarball's hash. A `git` source was tried first and fails in both Docker image
stages, which have no `git`:

```text
error: Failed to download and build `sqs-consumer @ git+https://github.com/sfitz42/sqs-consumer@755f135...`
  cause: Git executable not found. Ensure that Git is installed and available.
```

The tarball needs no Dockerfile change, but each image build now needs to reach GitHub. Both stages
build locally. This hasn't been through CDP's build pipeline.

## What does what

The library does the core: it long-polls, deletes a message when the handler returns, leaves it when
the handler raises, extends its visibility while the handler works, and lets work in progress finish
when stopping. It runs its polls on its own threads, so idle polls don't use up the pool that
`asyncio.to_thread` shares with the rest of the API.

`consumer.py` keeps the same `Consumer` interface as before, as a layer over the library. The
conversion handler is identical, and startup changes by one line.

| What the API needs | Our consumer | The library | How this branch does it |
|---|---|---|---|
| Delete on return, leave on raise | Yes | Yes | The library |
| Polls on their own threads | Yes | Yes | The library |
| Finish work in progress when stopping | Yes | Yes | The library |
| Keep a message hidden during long work | Yes, values read from the queue | Yes, with fixed values passed in | Reads the queue once on starting, then starts the library |
| One slow guide doesn't hold up others | A loop per message | A batch waits for all its messages, and its README suggests running more consumers | `CONVERSIONS_CONCURRENCY` consumers taking one message each |
| Tell the handler it's the last delivery | Yes | Gives the receive count if asked for it | Compares the count with the queue's limit |
| Refuse a queue with no dead-letter queue | Yes | No: `check_queue` only compares the heartbeat with the visibility timeout | Reads the settings before every poll |
| API starts with the queue down, and recovers | Yes | Yes, with `check_queue` off | Waits to start the library until the queue can be read |
| Say the queue has a problem | `healthy` | No: a failed poll is only logged, and no hook fires | Wraps the client passed to the library |
| Log an outage only as it starts and ends | Yes | No: each failed poll logs a warning with a stack trace | Wraps the client, so the library sees an empty poll. On a real queue, each outage logged twice: see below |
| Log a handler's failure | Yes | No: it goes only to the `on_processing_error` hook, though the README says errors are logged | A hook that logs it |

Passing a wrapped client through the library's `client=` option is the least tidy part of this. It
works, but it depends on how the library calls `receive_message`, and needs a `type: ignore`.

## What it saves

Lines of real code, leaving out blanks, comments and docstrings:

| | Our consumer | This branch |
|---|---|---|
| `consumer.py` | 101 | 127 |
| `sqs.py` | 83 | 45 |
| **Together** | **184** | **172** |

So 12 lines fewer, plus a dependency of 1,463 lines, most of which the API doesn't use: FIFO
ordering, SNS unwrapping, batch handlers and `RetryAfterError`. The tests grow by about 30 lines, mostly
because the fake queue now behaves like boto3's client.

**What would shrink the layer**, as requests for the library:

1. A hook for a failed poll, and one for a poll that succeeds again. This would remove the wrapped
   client, and most of the layer with it.
2. A log line when a handler fails, as its README says.
3. Heartbeat values read from the queue, and the queue's receive limit available to the handler.
4. A `check_queue` that also looks for a dead-letter queue, and doesn't stop a start when the queue
   is down.

Accepting less than like for like would also shrink it, without changing the library. The status
route could ask the queue directly when it's called, as `DEFRA/cads-bridge` does, and the heartbeat
could come from a setting. But every failed poll would then be logged, and a queue with no dead-letter
queue would be worked. We estimate that would take the layer to around 50 lines. We haven't tried it.

## What was tested

The same scenarios as the PR, with the tests adapted only as far as they had to be. The fake queue
answers as boto3's client does, so the library's real polling, heartbeat and delete code runs in them.

`uv run task test` passes with 961 tests, against 960 for the PR, and `uv run task openapi-check`
passes. Of 10 deliberate breaks to the layer, 3 got past the tests at first, so the fake queue now
honours `MaxNumberOfMessages`, a heartbeat timing test is tighter, and a test covers a queue losing its
dead-letter queue while running. All 10 are now caught.

On the local stack, with a real 73-picture guide, floci and MongoDB, the same 42 checks and the
stuck-save check pass on both:

| Scenario | Our consumer | This branch |
|---|---|---|
| `POST /guides` answers 202 | 0.02 s | 0.01 s |
| Save finishes, 74 of 74 parts | 1.63 s | 1.54 s |
| `/health` at its worst during the save | 16 ms | 28 ms |
| Five submissions at once | Converted once | Converted once |
| API killed part way through a save | At 30 of 74, finished 120 s later | At 31 of 74, finished 120 s later |
| A job failing every time: failure recorded | 242 s | 242 s |
| The same: on the dead-letter queue | 361 s | 361 s |
| Queue deleted: status route goes to 503 | 15.1 s | 8 to 18 s, over 5 runs |
| Queue back: status route goes to 200 | 1.5 s | 22 to 23 s, over 5 runs |
| Idle API stops | 20.6 s | 20.6 s |

Our consumer's timings are single runs on one 8-core machine, so small differences mean nothing.

**Two things the repeated outages showed on this branch:**

- **Recovery takes one full long poll.** `healthy` only turns true when a poll completes, and on an idle
  queue that takes 20 seconds. Our consumer has the same rule, so its single reading of 1.5 s is
  unexplained, and we haven't repeated it. It may be chance, with a poll already under way.
- **An outage is logged twice, not once.** 4 outages logged "cannot be worked" 8 times. The likely cause is that
  one consumer's poll fails, and another's that was already under way then succeeds, which marks the queue as
  healthy until that consumer's next poll fails. If so, the status route can also answer 200 briefly at
  the start of an outage. The tests use a queue in memory and don't show this. Our consumer logged once in its
  single run, but has the same rule, so it may do the same.

**Not tested:**

- Real SQS, or a CDP-sized container.
- The batch calls on dev. The library uses `delete_message_batch` and
  `change_message_visibility_batch`, where our consumer used the single calls. AWS authorises these
  by the same actions, and CDP grants those, but we haven't tried it.
- The library's own test suite, which we read but didn't run.
- The UI against this branch in a browser. It was checked in a browser against the PR's API.

## Before it could be used

- **It has no licence file.** The repo is public, but that on its face grants no permission to use it.
- **It's on a personal account,** with one commit and no CI in the repo. We found no DEFRA
  `pyproject.toml` naming it.
- **Each image build needs GitHub to answer,** and the tarball has to stay available.
- **Updates are by hand.** Moving to a new commit means changing the URL. We haven't checked whether
  Dependabot can follow a URL source.

Two smaller things from reading it:

- It requests the receive count with `AttributeNames`, which AWS's API reference marks as deprecated
  in favour of `MessageSystemAttributeNames`. It works on floci.
- `handler_timeout` could help with a conversion that hangs, but a conversion runs in a worker
  thread, and we'd expect a timeout not to stop the thread. We haven't tried it.

## Where this leaves us

1. Merge the PR with its own consumer: it's tested, and it has no new dependency.
2. Share this with the library's owner, and ask about its licence, where it will live and the requests
   above.
3. Swap once it has a licence and a release to pin. The first request alone would remove most of
   this layer.

To try this branch: `uv sync`, then `uv run task test`.
