import unittest
from nano_shell import policy


class DockerPolicyTests(unittest.TestCase):
    def test_read_only_daemon_inspection(self):
        for command in ('docker info', 'docker version', 'docker ps', 'docker ps -a',
                        'docker ps --all --quiet', 'docker ps --no-trunc'):
            with self.subTest(command=command):
                self.assertTrue(policy.validate_command(command))

    def test_daemon_mutations_globals_and_templates_rejected(self):
        for command in ('docker run alpine', 'docker exec container id', 'docker stop app',
                        'docker compose up', 'docker -H tcp://elsewhere info',
                        'docker ps --format {{.ID}}', 'docker info --format x', 'docker rm app'):
            with self.subTest(command=command):
                with self.assertRaises(policy.PolicyError):
                    policy.validate_command(command)


if __name__ == '__main__':
    unittest.main()
