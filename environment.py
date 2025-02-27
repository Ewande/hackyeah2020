import json
import logging

import gym
from gym import spaces
import numpy as np

import reporting
from utils import estimate_skills
import sys
from tabulate import tabulate
import copy

MEAN_START_SKILL_LEVEL = 20
STD_START_SKILL_LEVEL = 10

POPULATION_MEAN_SKILL_GAIN = 2
POPULATION_STD_SKILL_GAIN = 1.8
POPULATION_STD_TYPE_GAIN = 0.01
POPULATION_MIN_SKILL_GAIN = 0.1

STUDENT_SKILL_GAIN_STD = 0.2

TEST_SCORE_STD = 0.5

TARGET_SCORE = 95

REVIEW_RATIO = 0.25

REWARD_FOR_ACHIEVING_TARGET_LEVEL = 200
REWARD_FOR_ACHIEVING_ALL_LEVELS = 2000

PENALTY_FOR_UNNECESSARY_TEST = -700
TIME_PENALTY_FOR_TEST = - 2
GAIN_MULTIPLIER_FOR_TEST = 0

NOT_ADAPTED_DIFFICULTY_PENALTY = 0.25

GAIN_REWARD_RATIO = 0.1

PROPER_LEARNING_TYPE_REWARD = 0


class StudentEnv(gym.Env):
    def __init__(self, num_subjects=3, num_difficulty_levels=3, num_learning_types=3):
        super(StudentEnv).__init__()
        self.action_space = spaces.MultiDiscrete([
            2,  # train or test
            num_subjects,  # which subject the action refers to
            num_difficulty_levels,  # test difficulty level (not used if action=train)
            num_learning_types,  # train type (not used if action=test)
            num_difficulty_levels  # train difficulty level (not used if action=test)
        ])
        low_bound_observation_space_vector = np.array([
            0,  # min test score
            -100,  # min difference between previous test score and current test score (later called gain)
            *np.repeat(0, num_learning_types),  # min number of trainings since last test for each learning type
            *np.repeat(-100, num_learning_types),  # min gain attributed to each learning type
        ])
        high_bound_observation_space_vector = np.array([
            100,  # max test score
            100,  # max difference between previous test score and current test score (later called gain)
            *np.repeat(sys.maxsize, num_learning_types),  # max number of trainings since last test for each learning type
            *np.repeat(100, num_learning_types),  # max gain attributed to each learning type
        ])

        self.observation_space = spaces.Box(
            low=np.tile(low_bound_observation_space_vector, (num_subjects, num_difficulty_levels, 1)),
            high=np.tile(high_bound_observation_space_vector, (num_subjects, num_difficulty_levels, 1))
        )
        self.difficulties_levels = num_difficulty_levels
        self.learning_type_number = num_learning_types
        self.num_subjects = num_subjects
        self.skills_levels = np.maximum(
            np.random.normal(MEAN_START_SKILL_LEVEL, STD_START_SKILL_LEVEL, size=num_subjects), 0
        )
        self.last_scores = np.zeros(shape=(num_subjects, num_difficulty_levels, 2 * num_learning_types + 2))
        self.mean_skill_gains = _get_mean_skills_gains(num_subjects, num_learning_types)
        self.difficulties_thresholds = np.linspace(0, 100, num=num_difficulty_levels, endpoint=False)
        self.review_ratio = 1 / (num_difficulty_levels + 1)
        self.cumulative_train_time = np.zeros(num_subjects)
        self.train_counter = np.zeros((num_subjects, num_difficulty_levels, num_learning_types))
        self.episode = 0
        self.step_num = 0
        self.last_action = None

    def step(self, action):
        assert self.action_space.contains(action)
        is_test, subject, test_difficulty, learning_types, learning_difficulty = action
        difficulty_to_log = test_difficulty if is_test else learning_difficulty
        self.last_action = {
            'action': ['train', 'test'][is_test],
            'subject': subject + 1,
            'difficulty': difficulty_to_log + 1
        }

        if is_test:
            reward = self._test(subject, test_difficulty)
            self.cumulative_train_time[subject] = 0
            if (self.last_scores[:, -1, 0] > TARGET_SCORE).all():
                is_done = 1
                reward += REWARD_FOR_ACHIEVING_ALL_LEVELS
            else:
                is_done = 0
        else:
            reward = self._train(subject, learning_types, learning_difficulty)
            is_done = 0
        reward += - np.sqrt(self.step_num)
        self.last_action['reward'] = reward
        self.step_num += 1
        return self.last_scores, reward, is_done, {}

    def _test(self, subject, difficulty):
        test_mean = self._get_test_mean(subject, difficulty)
        previous_score = self.last_scores[subject, difficulty, 0]
        previous_scores = copy.copy(self.last_scores[:, :, 0])
        sampled_test_score = min(max(np.random.normal(test_mean, TEST_SCORE_STD), 0), 100)
        self.last_scores[subject, difficulty, 1] = sampled_test_score - previous_score
        self.last_scores[subject, difficulty, 0] = sampled_test_score
        estimated_improvement = estimate_skills(self.last_scores[:, :, 0], REVIEW_RATIO)[subject] - \
                                estimate_skills(previous_scores, REVIEW_RATIO)[subject]
        mean_type_gain = self._get_mean_type_gain(subject, difficulty, estimated_improvement)
        for d in range(self.difficulties_levels):
            self.last_scores[subject, d, -self.learning_type_number:] = mean_type_gain
            for s in range(self.num_subjects):
                self.last_scores[s, d, -self.learning_type_number:] = \
                    np.average([self.last_scores[s, d, -self.learning_type_number:], mean_type_gain],
                               weights=[1, 0.5], axis=0)

        self.last_scores[subject, difficulty, 2:2 + self.learning_type_number] = 0
        self.last_action['test_score'] = self.last_scores[subject, difficulty, 0]
        if not self.cumulative_train_time[subject]:
            return TIME_PENALTY_FOR_TEST
        if self.last_scores[subject, difficulty, 0] >= TARGET_SCORE:
            # if not self.skills_level_achieved[subject] and difficulty+1 == self.difficulties_level_nb:
            if previous_score < TARGET_SCORE:
                return REWARD_FOR_ACHIEVING_TARGET_LEVEL * (difficulty + 1) / self.difficulties_levels
            else:
                return PENALTY_FOR_UNNECESSARY_TEST
        return GAIN_MULTIPLIER_FOR_TEST * (1 / np.sqrt(self.step_num+1)) * (
                    (self.last_scores[subject, difficulty, 0] - previous_score)
                    / self.cumulative_train_time[subject]) + TIME_PENALTY_FOR_TEST

    def _get_mean_type_gain(self, subject, difficulty, estimated_improvement):
        num_trainings_since_last_test = self.last_scores[subject, difficulty, 2:2 + self.learning_type_number]
        if np.sum(num_trainings_since_last_test) > 0:
            ratio = num_trainings_since_last_test / np.sum(num_trainings_since_last_test)
            new_gain = estimated_improvement / np.sum(num_trainings_since_last_test)
            result = -np.ones(self.learning_type_number)
            for idx, elem in enumerate(
                    zip(self.last_scores[subject, difficulty, -self.learning_type_number:], ratio)):
                last_avg, ratio = elem
                if self.train_counter[subject, difficulty, idx] == 0:
                    result[idx] = new_gain
                else:
                    result[idx] = np.average([last_avg, new_gain],
                                             weights=[self.train_counter[subject, difficulty, idx],
                                                      ratio])
            for d in range(self.learning_type_number):
                self.train_counter[subject, d, :] += ratio
            return result
        else:
            return self.last_scores[subject, difficulty, -self.learning_type_number:]

    def _get_test_mean(self, subject, difficulty):
        proper_difficulty = self._get_proper_difficulty(self.skills_levels[subject])
        if proper_difficulty < difficulty:
            return self._get_too_hard_test_mean(subject, difficulty, proper_difficulty)
        if proper_difficulty > difficulty:
            return 100
        return self._get_proper_test_mean(subject, difficulty)

    def _get_too_hard_test_mean(self, subject, difficulty, proper_difficulty):
        review_mean = self._get_scaled_mean_score(subject, proper_difficulty)
        return self.review_ratio ** (difficulty - proper_difficulty) * review_mean

    def _get_proper_test_mean(self, subject, difficulty):
        proper_mean = self._get_scaled_mean_score(subject, difficulty)
        review_score = self.review_ratio if difficulty else 0
        return review_score*100 + proper_mean*(1-review_score)

    def _get_scaled_mean_score(self, subject, difficulty):
        return (self.skills_levels[subject] - self.difficulties_thresholds[difficulty]) * self.difficulties_levels

    def _train(self, subject, learning_type, learning_difficulty):
        mean_gain = self.mean_skill_gains[subject, learning_type]
        sampled_gain = np.random.normal(mean_gain, STUDENT_SKILL_GAIN_STD)
        adjusted_gain = sampled_gain * self._get_not_adapted_learning_penalty(
            self.skills_levels[subject], learning_difficulty)
        adjusted_gain = max(adjusted_gain, 0)
        self.skills_levels[subject] += adjusted_gain
        self.skills_levels[subject] = min(self.skills_levels[subject], 100)
        self.last_action['improvement'] = max(adjusted_gain, 0)
        self.last_action['learning_type'] = learning_type + 1
        self.cumulative_train_time[subject] += (learning_type + 1)
        self.last_scores[subject, learning_difficulty, 2 + learning_type] += 1
        estimated_skill = estimate_skills(self.last_scores[:, :, 0], REVIEW_RATIO)[subject]
        estimated_penalty = self._get_not_adapted_learning_penalty(estimated_skill, learning_difficulty)
        estimated_gain = POPULATION_MEAN_SKILL_GAIN * learning_type
        adapted_learning_reward = estimated_penalty * estimated_gain * GAIN_REWARD_RATIO
        predicted_excellence = np.argmax(np.sum(self.last_scores[:,:,-self.learning_type_number:], axis=(0, 1)))
        if learning_type == np.argmax(np.sum(self.mean_skill_gains, axis=0)) and predicted_excellence == learning_type:
            return PROPER_LEARNING_TYPE_REWARD #* 1/np.sqrt(self.step_num+1)
        return 0
        # return 0 - (learning_type + 1) + adapted_learning_reward

    def _get_not_adapted_learning_penalty(self, skill, learning_difficulty):
        proper_difficulty = self._get_proper_difficulty(skill)
        return NOT_ADAPTED_DIFFICULTY_PENALTY ** abs(learning_difficulty - proper_difficulty)

    def _get_proper_difficulty(self, skill):
        return sum(self.difficulties_thresholds <= skill) - 1

    def reset(self):
        self.skills_levels = np.maximum(
            np.random.normal(MEAN_START_SKILL_LEVEL, STD_START_SKILL_LEVEL, size=len(self.skills_levels)),
            np.zeros_like(self.skills_levels)
        )
        self.last_scores = np.zeros_like(self.last_scores)
        self.mean_skill_gains = _get_mean_skills_gains(*self.mean_skill_gains.shape)
        self.difficulties_thresholds = np.linspace(0, 100, num=self.difficulties_levels, endpoint=False)
        self.cumulative_train_time = np.zeros_like(self.cumulative_train_time)
        self.train_counter = np.zeros_like(self.train_counter)
        self.episode += 1
        self.step_num = 0
        return self.last_scores

    def render(self, mode='human'):
        action_to_str = ';'.join(f'{k}={v}' for k, v in self.last_action.items())
        last_scores = self.last_scores
        types = {f'Learning type number {i + 1}': last_scores[:, :, -self.learning_type_number + i].round(3)
                 for i in range(self.learning_type_number)}
        table = {'Test matrix': last_scores[:, :, 0].round(1)}
        table.update(types)
        if self.last_action['action'] == 'test':
            table.update({f'Train counters {i + 1}': last_scores[:, :, 2 + i].round(3)
                          for i in range(self.learning_type_number)})
        print(f'***\n'
              f'Action: {action_to_str}\n' +
              tabulate(table, headers='keys') + '\n'
              f'Latent skill level: {self.skills_levels.round(1)}\n'
                                                f'***')
        logging.info(json.dumps({**self.last_action, 'skills': self.skills_levels, 'step': self.step_num,
                                 'episode': self.episode, 'env': 'original'},
                                cls=reporting.NpEncoder))
        return self.last_action


def _get_mean_skills_gains(subjects_number, learning_types_number):
    # skill_gain_matrix = np.tile(np.random.normal(POPULATION_MEAN_SKILL_GAIN, POPULATION_STD_SKILL_GAIN,
    #                                              size=(learning_types_number)), (subjects_number, 1))
    # skill_gain_matrix += np.random.normal(0, POPULATION_STD_TYPE_GAIN, size=(subjects_number, learning_types_number))
    # interval_mean_skill_gains = np.maximum(
    #     skill_gain_matrix,
    #     np.full(shape=(subjects_number, learning_types_number), fill_value=POPULATION_MIN_SKILL_GAIN)
    # )

    if np.random.random()>0.5:
        excellence_skills = np.tile(
            np.concatenate(([np.random.normal(3, 0.2)], np.random.normal(0.3, 0.1, 2))), (3, 1))
        excellence_skills += np.random.normal(0, 0.05, size=(3, 3))
        excellence_skills = np.maximum(
            excellence_skills,
            np.full(shape=(3, 3), fill_value=0.05)
        )
    else:
        excellence_skills = np.tile(
            np.concatenate((np.random.normal(0.2, 0.1, 2), [np.random.normal(3, 0.2)])), (3, 1))
        excellence_skills += np.random.normal(0, 0.05, size=(3, 3))
        excellence_skills = np.maximum(
            excellence_skills,
            np.full(shape=(3, 3), fill_value=0.05)
        )
    interval_mean_skill_gains = excellence_skills
    return interval_mean_skill_gains


class StudentEnvBypass(StudentEnv):
    def __init__(self, studentenvcopy, prob_ratio=None):
        num_subjects, num_difficulty_levels, num_learning_types = studentenvcopy.num_subjects, \
                                                                  studentenvcopy.difficulties_levels, \
                                                                  studentenvcopy.learning_type_number
        super(StudentEnvBypass, self).__init__(num_subjects, num_difficulty_levels, num_learning_types)
        self.last_scores = copy.deepcopy(studentenvcopy.last_scores)
        self.cumulative_train_time = copy.deepcopy(studentenvcopy.cumulative_train_time)
        self.train_counter = copy.deepcopy(studentenvcopy.train_counter)
        self.episode = copy.deepcopy(studentenvcopy.episode)
        self.step_num = copy.deepcopy(studentenvcopy.step_num)
        self.prob_ratio = prob_ratio if prob_ratio else [0.8, 0.1, 0.1]
        self.mean_skill_gains = copy.deepcopy(studentenvcopy.mean_skill_gains)
        self.skills_levels = copy.deepcopy(studentenvcopy.skills_levels)

    def step(self, action):
        assert self.action_space.contains(action)
        is_test, subject, test_difficulty, learning_types, learning_difficulty = action
        difficulty_to_log = test_difficulty if is_test else learning_difficulty
        self.last_action = {
            'action': ['train', 'test'][is_test],
            'subject': subject + 1,
            'difficulty': difficulty_to_log + 1
        }

        if is_test:
            reward = self._test(subject, test_difficulty)
            self.cumulative_train_time[subject] = 0
            if (self.last_scores[:, -1, 0] > TARGET_SCORE).all():
                is_done = 1
                reward += REWARD_FOR_ACHIEVING_ALL_LEVELS
            else:
                is_done = 0
        else:
            learning_types = int(np.random.choice(self.difficulties_levels, p=self.prob_ratio))
            reward = self._train(subject, learning_types, learning_difficulty)
            is_done = 0
        reward += - np.sqrt(self.step_num)
        self.last_action['reward'] = reward
        self.step_num += 1
        return self.last_scores, reward, is_done, {}

    def render(self, mode='human'):
        action_to_str = ';'.join(f'{k}={v}' for k, v in self.last_action.items())
        last_scores = self.last_scores
        types = {f'Learning type number {i + 1}': last_scores[:, :, -self.learning_type_number + i].round(3)
                 for i in range(self.learning_type_number)}
        table = {'Test matrix': last_scores[:, :, 0].round(1)}
        table.update(types)
        if self.last_action['action'] == 'test':
            table.update({f'Train counters {i + 1}': last_scores[:, :, 2 + i].round(3)
                          for i in range(self.learning_type_number)})
        print(f'***\n'
              f'Action: {action_to_str}\n' +
              tabulate(table, headers='keys') + '\n'
              f'Latent skill level: {self.skills_levels.round(1)}\n'
                                                f'***')
        logging.info(json.dumps({**self.last_action, 'skills': self.skills_levels, 'step': self.step_num,
                                 'episode': self.episode,
                                 'env': f'bias for {np.argmax(self.prob_ratio)+1} learning type'},
                                cls=reporting.NpEncoder))
        return self.last_action


