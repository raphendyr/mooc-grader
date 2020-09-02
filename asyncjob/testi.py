from celery import Celery
from celery import bootsteps
from kombu import Consumer, Exchange, Queue

my_queue = Queue('custom', Exchange('custom'), 'routing_key')

app = Celery(broker='amqp://172.17.0.2')


class MyConsumerStep(bootsteps.ConsumerStep):

    def get_consumers(self, channel):
        return [Consumer(channel,
                         queues=[my_queue],
                         callbacks=[self.handle_message],
                         accept=['json'])]

    def handle_message(self, body, message):
        print('Pod {}: {} ({})'.format(
            body.get('meta', {}).get('pod_name'),
            body.get('state', '?'),
            message.properties.get('correlation_id', ''),
        ))
        print("  meta:", body.get('meta'))
        print("  times:", body.get('times'))

        message.ack()
app.steps['consumer'].add(MyConsumerStep)


def send_me_a_message(who, producer=None):
    with app.producer_or_acquire(producer) as producer:
        producer.publish(
            {'hello': who},
            serializer='json',
            exchange=my_queue.exchange,
            routing_key='routing_key',
            declare=[my_queue],
            retry=True,
        )

if __name__ == '__main__':
    print("start..")
    send_me_a_message('world!')
    print("message send..")
    #app.send_task('custom', (1, 2))
