from common_fixtures import *  # NOQA
from docker import Client

CONTAINER_APPEAR_TIMEOUT_MSG = 'Timed out waiting for container ' \
                               'to appear. Name: [%s].'

TEST_IMAGE = 'ibuildthecloud/helloworld'

socat_test_image = os.environ.get('CATTLE_CLUSTER_SOCAT_IMAGE',
                                  'docker:sonchang/socat-test')


@pytest.fixture(scope='module')
def docker_client(client, unmanaged_network, request):
    # When these tests run in the CI environment, the hosts don't expose the
    # docker daemon over tcp, so we need to create a container that binds to
    # the docker socket and exposes it on a port
    hosts = client.list_host(kind='docker', removed_null=True)
    assert len(hosts) >= 1
    host = hosts[0]
    socat_container = client.create_container(
        name='socat-%s' % random_str(),
        networkIds=[unmanaged_network.id],
        imageUuid=socat_test_image,
        ports='2375',
        stdinOpen=False,
        tty=False,
        publishAllPorts=True,
        dataVolumes='/var/run/docker.sock:/var/run/docker.sock',
        requestedHostId=host.id)

    def remove_socat():
        client.delete(socat_container)

    request.addfinalizer(remove_socat)

    wait_for_condition(
        client, socat_container,
        lambda x: x.state == 'running',
        lambda x: 'State is: ' + x.state)

    socat_container = client.reload(socat_container)
    ip = host.ipAddresses()[0].address
    port = socat_container.ports()[0].publicPort

    params = {}
    params['base_url'] = 'tcp://%s:%s' % (ip, port)
    api_version = os.getenv('DOCKER_API_VERSION', '1.15')
    params['version'] = api_version

    return Client(**params)


@pytest.fixture(scope='module')
def pull_images(docker_client):
    image = (TEST_IMAGE, 'latest')
    images = docker_client.images(image[0])
    if not images:
        docker_client.pull(image[0], image[1])


def test_native_unmanaged_network(docker_client, admin_client, pull_images):
    container = None
    try:
        name = 'native-%s' % random_str()
        d_container = docker_client.create_container(TEST_IMAGE,
                                                     name=name)
        docker_client.start(d_container)
        inspect = docker_client.inspect_container(d_container)

        def check():
            containers = admin_client.list_container(name=name)
            return len(containers) > 0

        wait_for(check, timeout_message=CONTAINER_APPEAR_TIMEOUT_MSG % name)

        r_containers = admin_client.list_container(name=name)
        assert len(r_containers) == 1
        container = r_containers[0]
        container = admin_client.wait_success(container)

        assert container.externalId == d_container['Id']
        assert container.state == 'running'
        assert container.primaryIpAddress == inspect['NetworkSettings'][
            'IPAddress']
    finally:
        cleanup_container(admin_client, container)


def test_native_managed_network(docker_client, admin_client, super_client,
                                pull_images):
    container = None
    try:
        name = 'native-%s' % random_str()
        d_container = docker_client. \
            create_container(TEST_IMAGE,
                             name=name,
                             environment=['RANCHER_NETWORK=true'])
        docker_client.start(d_container)
        inspect = docker_client.inspect_container(d_container)

        def check():
            containers = admin_client.list_container(name=name)
            return len(containers) > 0

        wait_for(check, timeout_message=CONTAINER_APPEAR_TIMEOUT_MSG % name)

        r_containers = admin_client.list_container(name=name)
        assert len(r_containers) == 1
        container = r_containers[0]
        container = admin_client.wait_success(container, timeout=180)

        assert container.externalId == d_container['Id']
        assert container.state == 'running'
        assert container.primaryIpAddress != inspect['NetworkSettings'][
            'IPAddress']
        nics = super_client.reload(container).nics()
        assert len(nics) == 1
        assert container.primaryIpAddress == nics.data[0].ipAddresses().data[
            0].address

        # Let's test more of the life cycle
        container = admin_client.wait_success(container.stop(timeout=0))
        assert container.state == 'stopped'

        container = admin_client.wait_success(container.start(timeout=0))
        assert container.state == 'running'

        container = admin_client.wait_success(container.restart(timeout=0))
        assert container.state == 'running'

        container = admin_client.wait_success(container.stop(timeout=0))
        assert container.state == 'stopped'

        container = admin_client.wait_success(container.remove(timeout=0))
        assert container.state == 'removed'

        container = admin_client.wait_success(container.purge(timeout=0))
        assert container.state == 'purged'
    finally:
        cleanup_container(admin_client, container)


def test_native_not_started(docker_client, admin_client, super_client,
                            pull_images):
    container = None
    try:
        name = 'native-%s' % random_str()
        d_container = docker_client. \
            create_container(TEST_IMAGE, name=name,
                             environment=['RANCHER_NETWORK=true'])

        def check():
            containers = admin_client.list_container(name=name)
            return len(containers) > 0

        wait_for(check, timeout_message=CONTAINER_APPEAR_TIMEOUT_MSG % name)

        r_containers = admin_client.list_container(name=name)
        assert len(r_containers) == 1
        container = r_containers[0]
        container = admin_client.wait_success(container)
        c_id = container.id

        assert container.externalId == d_container['Id']
        assert container.state == 'running'

        def stopped_check():
            c = admin_client.by_id_container(c_id)
            return c.state == 'stopped'

        wait_for(stopped_check,
                 'Timeout waiting for container to stop. Id: [%s]' % c_id)

        nics = super_client.reload(container).nics()
        assert len(nics) == 1
        assert container.primaryIpAddress == nics.data[0].ipAddresses().data[
            0].address
    finally:
        cleanup_container(admin_client, container)


def test_native_removed(docker_client, admin_client, pull_images):
    container = None
    try:
        name = 'native-%s' % random_str()
        d_container = docker_client.create_container(TEST_IMAGE,
                                                     name=name)
        docker_client.remove_container(d_container)

        def check():
            containers = admin_client.list_container(name=name)
            return len(containers) > 0

        wait_for(check, timeout_message=CONTAINER_APPEAR_TIMEOUT_MSG % name)

        r_containers = admin_client.list_container(name=name)
        assert len(r_containers) == 1
        container = r_containers[0]
        container = admin_client.wait_success(container)

        assert container.externalId == d_container['Id']
    finally:
        cleanup_container(admin_client, container)


def test_native_volumes(docker_client, admin_client, pull_images):
    container = None
    try:
        name = 'native-%s' % random_str()
        d_container = docker_client.create_container(TEST_IMAGE,
                                                     name=name,
                                                     volumes=['/foo',
                                                              '/host/var'])
        docker_client.start(d_container,
                            binds={'/var': {'bind': '/host/var'}})

        def check():
            containers = admin_client.list_container(name=name)
            return len(containers) > 0

        wait_for(check, timeout_message=CONTAINER_APPEAR_TIMEOUT_MSG % name)

        r_containers = admin_client.list_container(name=name)
        assert len(r_containers) == 1
        container = r_containers[0]
        container = admin_client.wait_success(container)

        assert container.externalId == d_container['Id']
        assert container.state == 'running'
        mounts = container.mounts()
        assert len(mounts) == 2

        mount = mounts[0]
        assert mount.path == '/foo'
        volume = mount.volume()
        assert not volume.isHostPath

        mount = mounts[1]
        assert mount.path == '/host/var'
        volume = mount.volume()
        assert volume.isHostPath
        assert volume.uri == 'file:///var'
    finally:
        cleanup_container(admin_client, container)


def cleanup_container(admin_client, container):
    try:
        if container:
            admin_client.delete(container)
    except:
        pass