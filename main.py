import json

import click
from stable_baselines.common.policies import MlpPolicy
from stable_baselines import TRPO, PPO2

import agent
from environment import StudentEnv, StudentEnvTalented, StudentEnvBypass
from reporting import setup_logging


SUPPORTED_MODEL_TYPES = {
    'ppo2': PPO2,
    'trpo': TRPO,
}

SUPPORTED_ENV_TYPES = {
    'StudentEnv': StudentEnv,
    'StudentEnvTalented': StudentEnvTalented,
    'StudentEnvBypass': StudentEnvBypass
}

@click.group()
def cli():
    pass


@cli.command()
@click.argument('model-type', type=click.Choice(list(SUPPORTED_MODEL_TYPES.keys()), case_sensitive=False))
@click.argument('output-path')
@click.option('--num-subjects', '-s', default=2)
@click.option('--num-difficulty-levels', '-d', default=3)
@click.option('--num-learning-types', '-l', default=3)
@click.option('--training-steps', '-t', default=250000)
@click.option('--env_type', '-v', type=click.Choice(list(SUPPORTED_ENV_TYPES.keys()), case_sensitive=False),
              default='StudentEnv')
def train(model_type, output_path, num_subjects, num_difficulty_levels, num_learning_types, training_steps, env_type):
    model_class = SUPPORTED_MODEL_TYPES[model_type]
    env_class = SUPPORTED_ENV_TYPES[env_type]
    env = env_class(num_subjects, num_difficulty_levels, num_learning_types)

    model = model_class(MlpPolicy, env, verbose=1, gamma=0.9)
    model.learn(total_timesteps=training_steps)
    model.save(output_path)
    with open(output_path + '.metadata', 'w') as outfile:
        json.dump({
            'model_type': model_type,
            'num_subjects': num_subjects,
            'num_difficulty_levels': num_difficulty_levels,
            'num_learning_types': num_learning_types,
        }, outfile)


@cli.command()
@click.argument('model-path', type=click.Path())
@click.option('--num-episodes', '-e', default=200)
@click.option('--num-steps', '-s', default=20000)
@click.option('--logging-path')
@click.option('--env_type', '-v', type=click.Choice(list(SUPPORTED_ENV_TYPES.keys()), case_sensitive=False),
              default='StudentEnv')
def test(model_path, num_episodes, num_steps, logging_path, env_type):
    env, metadata = _setup_run(model_path, logging_path, env_type)
    model = SUPPORTED_MODEL_TYPES[metadata['model_type']].load(model_path)
    _run_env(model, env, num_episodes, num_steps)


@cli.command()
@click.option('--metadata-path', type=click.Path(exists=True))
@click.option('--num-episodes', '-e', default=200)
@click.option('--num-steps', '-s', default=20000)
@click.option('--logging-path')
@click.option('--env_type', '-v', type=click.Choice(list(SUPPORTED_ENV_TYPES.keys()), case_sensitive=False),
              default='StudentEnv')
def test_random(metadata_path, num_episodes, num_steps, logging_path, env_type):
    env, metadata = _setup_run(metadata_path, logging_path, env_type)
    model = agent.RandomAgent(env)
    _run_env(model, env, num_episodes, num_steps)


@cli.command()
@click.option('--metadata-path', type=click.Path())
@click.option('--num-episodes', '-e', default=200)
@click.option('--num-steps', '-s', default=20000)
@click.option('--logging-path')
@click.option('--env_type', '-v', type=click.Choice(list(SUPPORTED_ENV_TYPES.keys()), case_sensitive=False),
              default='StudentEnv')
def test_simple(metadata_path, num_episodes, num_steps, logging_path, env_type):
    env, metadata = _setup_run(metadata_path, logging_path, env_type)
    model = agent.SimpleAgent(env)
    _run_env(model, env, num_episodes, num_steps)


def _setup_run(input_path, logging_path, env_type):
    with open(input_path + '.metadata') as outfile:
        metadata = json.load(outfile)

    setup_logging(logging_path or f'{input_path}.log')
    env_class = SUPPORTED_ENV_TYPES[env_type]
    env = env_class(num_subjects=metadata['num_subjects'])
    return env, metadata


def _run_env(model, env, num_episodes, num_steps):
    for ep in range(num_episodes):
        i = _run_episode(model, env, num_steps)
        print(ep, i)
        print(env.mean_skill_gains)
        print('-'*90+' END OF TRAINING FOR ONE STUDENT '+'-'*90)


def _run_episode(model, env, num_steps):
    obs = env.reset()
    for i in range(num_steps):
        action, _states = model.predict(obs)
        obs, rewards, done, info = env.step(action)
        print(i)
        env.render()
        if done:
            return i


if __name__ == '__main__':
    cli()
