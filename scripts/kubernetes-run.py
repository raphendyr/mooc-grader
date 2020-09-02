#!/usr/bin/env python3

# https://github.com/kubernetes-client/python
# https://github.com/kubernetes-client/python/tree/master/kubernetes <-- create_namespaced_pod

import json
import re
import sys
import unicodedata

from kubernetes import client as c, config
from kubernetes.client.rest import ApiException

def makeValidLabel(l):
    # get string
    if isinstance(l, dict):
        l = l.get('en') or l.values()[0]
    # drop dots from letters, i.e. replace umlauts
    l = unicodedata.normalize('NFKD', l).encode('ascii','ignore').decode('ascii')
    l = l.replace(' ', '_')                 # substitute space with underscore
    l = re.sub('^[^a-z0-9A-Z]*', '', l)     # must start with alnphanumeric
    l = re.sub('[^a-zA-Z0-9_.-]', '', l)    # the rest can be alphanumeric, -, _ or .
    return l[:62]                           # must be less than 63 chars

def kubernetes_run(arguments):
    config.load_kube_config("/srv/grader/minikube.config")

    # Parse arguments
    SID, GRADER_HOST, DOCKER_IMAGE, EX_MOUNT, SUBM_MOUNT, CMD, course_config, exercise_config = arguments[1:]
    course_config = json.loads(course_config)
    exercise_config = json.loads(exercise_config)

    # Create some default vars
    GRADER_NAME = makeValidLabel('/'.join(GRADER_HOST.split('/', 2)[2:]).replace(':', '-').replace('/', '_'))
    COURSE_LABEL = makeValidLabel(course_config.get('name', ''))
    EXERCISE_LABEL = makeValidLabel(exercise_config.get('title', ''))
    DEFAULT_CPU = 1
    DEFAULT_MEM = "1Gi"
    
    # Setup volumes & mounts
    volumes = [
        c.V1Volume(name='run', empty_dir=c.V1EmptyDirVolumeSource(medium='Memory', size_limit='100Mi')),
        c.V1Volume(name='submission', empty_dir=c.V1EmptyDirVolumeSource(size_limit='1Gi')),
        c.V1Volume(name='exercise', empty_dir=c.V1EmptyDirVolumeSource()),
    ]
    volume_mounts = [
        c.V1VolumeMount(name='run', mount_path='/run'),
        c.V1VolumeMount(name='submission', mount_path='/submission'),
        c.V1VolumeMount(name='exercise', mount_path='/exercise'),
    ]

    # Personalized exercises config
    if exercise_config.get("personalized_exercise", None) != None:
        volumes.append( c.V1Volume(name='personalized', empty_dir=c.V1EmptyDirVolumeSource()) )
        volume_mounts.append( c.V1VolumeMount(name='personalized', mount_path='/personalized_exercise') )

    # Prepare pod & pod metadata
    namespace = 'grader'
    #-if DOCKER_IMAGE.startswith("cse4100/mccdind"):
    #-  namespace = 'cse4100-special'
    pod = c.V1Pod()
    pod.metadata = c.V1ObjectMeta(
        generate_name='grader-',
        namespace=namespace,
        labels={
            'mooc-grader': GRADER_NAME,
            'course': COURSE_LABEL,
            'exercise': EXERCISE_LABEL,
        }
    )

    # Initial resources config
    resource_config = exercise_config.get("resources", {})
    mem = resource_config.get("memory", DEFAULT_MEM)
    cpu = resource_config.get("cpu", DEFAULT_CPU)

    #-securityContext = {}
    #-nodeSelector = {}
    #-tolerations = []
    #-if DOCKER_IMAGE.startswith("registry.cs.aalto.fi/aplus/matlab"):
    #-    securityContext = { "capabilities": { "add": [ "NET_ADMIN" ] } }
    #-    nodeSelector = { "kubernetes.io/hostname": "k8s-node4.cs.aalto.fi" }
    #-if DOCKER_IMAGE.startswith("cse4100/android-grader") or DOCKER_IMAGE.startswith("cse4100/mccdind"):
    #-    cpu = 4
    #-    mem = "8Gi"
    #-    securityContext = { "privileged": True }
    #-    nodeSelector = { "cs-aalto/app": "privileged-grading" }
    #-    tolerations = [{
    #-      "key": "cs-aalto/app",
    #-      "value": "privileged-grading",
    #-      "operator": "Equal",
    #-      "effect": "NoSchedule"
    #-    }]

    # Finalized resource config
    resources = c.V1ResourceRequirements(
        requests={ "cpu": cpu/2, "memory": '128Mi' },
        limits={ "cpu": cpu, "memory": mem },
    )

    # Define PodSpec
    pod.spec = c.V1PodSpec(
        #active_deadline_seconds=1800,
        active_deadline_seconds=20,
        init_containers=[c.V1Container(
            name='download',
            image='init-container',
            volume_mounts=volume_mounts,
            image_pull_policy="IfNotPresent",
            resources=resources,
            env=[c.V1EnvVar(name="SID", value=SID), c.V1EnvVar(name="REC", value=GRADER_HOST)],
            #-security_context=securityContext
        )],
        containers=[c.V1Container(
            name='grade',
            image=DOCKER_IMAGE,
            #-preStop is killed, when main process dies -> preStop doesn't help us
            #- a) add grade to grade-wrapper on SIGTERM
            #- b) get data in pod event watcher, after pod dies
            #-lifecycle=c.V1Lifecycle(
            #-    pre_stop=c.V1Handler( _exec=c.V1ExecAction(command=['grade']) )
            #-),
            args=[CMD],
            volume_mounts=volume_mounts,
            image_pull_policy="IfNotPresent",
            resources=resources,
            env=[c.V1EnvVar(name="SID", value=SID), c.V1EnvVar(name="REC", value=GRADER_HOST)],
            #-security_context=securityContext
        )],
        #-node_selector=nodeSelector,
        volumes=volumes,
        restart_policy="Never",
        #-tolerations=tolerations,
        #-image_pull_secrets=[{ "name": "mooc-grader-regkey" }]
        automount_service_account_token=False,
        enable_service_links=False,
    )

    # Configure 'constant load environment' exercise
    # NOTE: Requires at least one backend node to be labeled + tainted appropriately
    if exercise_config.get("require_constant_environment", False) != False:
        # Label the pod
        pod.metadata.labels["cs-aalto/app"] = "constant-env-grading"
        # Disallow multiple pods of this type from running on a node
        pod.spec.affinity=c.V1Affinity(
            pod_anti_affinity={
                "requiredDuringSchedulingIgnoredDuringExecution": [{
                    "labelSelector": {
                        "matchLabels": {
                            "cs-aalto/app": "constant-env-grading"
                        }
                    },
                    "topologyKey": "kubernetes.io/hostname"
                }]
            }
        )
        # Select only from dedicated nodes
        pod.spec.node_selector = {
            "cs-aalto/app": "constant-env-grading"
        }
        # Allow running on the dedicated node
        pod.spec.tolerations = [{
            "key": "cs-aalto/app",
            "operator": "Equal",
            "value": "constant-env-grading"
        }]    
    
    v1 = c.CoreV1Api()
    try:
        res = v1.create_namespaced_pod(namespace, pod)
    except ApiException as err:
        message = err.reason
        if err.body:
            try:
                body = json.loads(err.body)
            except ValueError:
                pass
            else:
                if body.get('message'):
                    message += " | %s" % (body['message'],)
        print("Kubernetes Error:", message, file=sys.stderr)
        return 1
    else:
        print("pod-name=%s" % (res.metadata.name,))
        return 0


if __name__ == '__main__':
    sys.exit(kubernetes_run(sys.argv) or 0)

