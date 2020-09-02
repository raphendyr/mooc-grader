"""Watch multiple K8s event streams without threads."""
import asyncio
from datetime import datetime, timezone

import aiormq
from kubernetes_asyncio import client, config, watch

from pprint import pprint

from enum import Enum
from json import dumps


class ContainerState(Enum):
    # ok:
    SUCCEEDED = 0
    # failed:
    CRASHED = 1
    EXPIRED = 2
    # error:
    UNKNOWN = 99


EVENT_QUEUE = asyncio.Queue(maxsize=100) # FIXME:

class Stop:
    pass
Stop = Stop()


async def watch_namespaces():
    v1 = client.CoreV1Api()
    async with watch.Watch().stream(v1.list_namespace) as stream:
        async for event in stream:
            etype, obj = event['type'], event['object']
            if hasattr(obj, 'metadata'):
                print("{} namespace {}".format(etype, obj.metadata.name))


async def watch_pods():
    v1 = client.CoreV1Api()
    async with watch.Watch().stream(v1.list_pod_for_all_namespaces) as stream:
        async for event in stream:
            #pprint(event)
            evt, obj = event['type'], event['object']
            print("{} pod {}/{} ({} {}): {} {}".format(
                evt, obj.metadata.namespace, obj.metadata.name,
                obj.metadata.uid, obj.metadata.resource_version,
                obj.status.phase, obj.status.reason))
            if obj.status.phase not in ('Succeeded', 'Failed'):
                continue

            if obj.status.phase == 'Failed':
                s = ContainerState.EXPIRED if obj.status.reason == 'DeadlineExceeded' else ContainerState.CRASHED
            elif obj.status.phase == 'Succeeded':
                s = ContainerState.SUCCEEDED
            else:
                s = ContainerState.UNKNOWN

            meta = {
                'phase': obj.status.phase,
                'reason': obj.status.reason,
                'pod_name': obj.metadata.name,
                'pod_id': obj.metadata.uid,
            }

            times = {
                # download images
                'started': obj.status.start_time,
                # download submission data
                'init_start': obj.status.init_container_statuses[0].state.terminated.started_at,
                'init_end': obj.status.init_container_statuses[-1].state.terminated.finished_at,
                # execute
                'main_start': min((x.state.terminated.started_at if x.state.terminated else x.state.running.started_at if x.state.running else None) for x in obj.status.container_statuses),
                'main_end': max((x.state.terminated.finished_at if x.state.terminated else datetime.now(timezone.utc))for x in obj.status.container_statuses),
            }
            times = {k: v.isoformat() if v else None for k, v in times.items()}

            data = {'state': str(s), 'meta': meta, 'times': times}
            #pprint(data)
            #print(dumps(data))
            await EVENT_QUEUE.put(data)
            print("Added to queue, qsize=", EVENT_QUEUE.qsize())


async def deliver_events():
    while True:
        #connection = await aiormq.connect("amqp://guest:guest@172.17.0.2/")
        print(" > connecting amqp..")
        connection = await aiormq.connect("amqp://kube:kube@172.20.0.2/")
        try:
            print(" > opeting amqp channel..")
            channel = await connection.channel()

            print(" > waiting for queue events..")
            while True:
                event = await EVENT_QUEUE.get()
                if event is Stop:
                    print("Consumer stop!")
                    raise asyncio.CancelledError()

                pprint(event)
                msg = dumps(event)
                await channel.basic_publish(msg.encode('UTF-8'),
                    exchange='kubernetes_events',
                    routing_key='pod_events',
                    properties=aiormq.spec.Basic.Properties(
                        content_encoding='utf-8',
                        content_type='application/json',
                        delivery_mode=2, # 1 non persistent (def), 2 persistent
                        #headers=,
                        correlation_id=event['meta']['pod_id'],
                        priority=0,
                    ),
                )
        except asyncio.CancelledError as err:
            await connection.close(err)
            raise
        except Exception as err:
            await connection.close(err)
            print("Reconnecting AQMP...")


async def stop_queues():
    await EVENT_QUEUE.put(Stop)


def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(config.load_kube_config())

    producers = asyncio.gather(*[
        watch_namespaces(),
        watch_pods(),
    ])
    consumers = asyncio.gather(*[
        deliver_events(),
    ])
    all_tasks = asyncio.gather(producers, consumers)

    try:
        loop.run_until_complete(all_tasks)
    except KeyboardInterrupt as e:
        print("Caught keyboard interrupt. Canceling tasks...")
    finally:
        try:
            # stop the pipeline
            print("Exit 1: cancel producers..")
            producers.cancel()
            # wait consumers to empty queues...
            print("Exit 2: waiting consumers to die...")
            try:
                loop.run_until_complete(asyncio.gather(
                    stop_queues(),
                    asyncio.wait_for(consumers, timeout=10),
                ))
            except:
                pass
            # kill remaining consumers
            print("Exit 3: cancelling consumers")
            consumers.cancel()
            # run remaining tasks..
            remaining_tasks = asyncio.all_tasks(loop)
            if remaining_tasks:
                print("Exit 3: %d pending tasks.." % (len(remaining_tasks), ))
                loop.run_until_complete(asyncio.wait_for(
                    asyncio.gather(*remaining_tasks, return_exceptions=True),
                    timeout=10,
                ))
                for task in remaining_tasks:
                    exc = task.exception()
                    if exc is not None:
                        loop.call_exception_handler({
                            'exception': exc,
                            'message': 'unhandled exception during shutdown',
                            'task': task,
                        })
            # all tasks should be cancelled now...
            all_tasks.exception() # ignore exception, basically CancelledError
            # final steps..
            print("Exit 4: Shutting down..")
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()



if __name__ == '__main__':
    main()
