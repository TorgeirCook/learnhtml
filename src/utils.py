import functools
import itertools
import tempfile
from urllib.parse import urlparse

import keras
import numpy as np
import pandas as pd
from keras.callbacks import Callback
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.model_selection import GridSearchCV
from sklearn.utils import class_weight

from keras_utils import sparse_generator, KerasSparseClassifier


def get_domain_from_url(url):
    """Returns the fully-qualified domain fo an url."""
    return urlparse(url).netloc


def zip_dicts(*dicts):
    """Given a list of dictionaries, zip their corresponding
    values and return the resulting dictionary"""
    return {key: [dictionary[key] for dictionary in dicts] for key in dicts[0].keys()}


def dict_combinations(*dict_list_lists):
    """Given a list of lists of dictionaries return the list of
    all possible dictionaries resulting from the unions of sampling
    a dictionary from each list."""
    combinations = itertools.product(*dict_list_lists)  # cartesian product of all
    return map(lambda comb: functools.reduce(lambda a, b: dict(list(a.items()) + list(b.items())),
                                             comb, {}), combinations)  # return resulting dicts


def get_metrics(estimator, big_X, big_y, train_ind, validation_ind, test_ind, hyperparams={}):
    """Returns a datafram of results containing the score for train, test and validation
    for the given estimator. Optionally tunes with a search space of given hyperparameters."""
    # create a big set that is split just by the normal split
    split = [(train_ind, validation_ind)]
    # define the grid search with the goal to maximize f1 score on validation
    grid_search = GridSearchCV(estimator=estimator, param_grid=hyperparams, scoring='f1',
                               cv=split, verbose=2, pre_dispatch=1)
    grid_search.fit(big_X, big_y)

    result_df = pd.DataFrame(data=[
        {'f1-score': f1_score(big_y[train_ind], grid_search.predict(big_X[train_ind, :])), 'set': 'train'},
        {'f1-score': f1_score(big_y[validation_ind], grid_search.predict(big_X[validation_ind, :])),
         'set': 'validation'},
        {'f1-score': f1_score(big_y[test_ind], grid_search.predict(big_X[test_ind, :])), 'set': 'test'}
    ])
    return result_df, grid_search


def dict_combinations(*dict_list_lists):
    """Given a list of lists of ditionaries return the list of
    all posible dictionarie resulting from the unions of sampling
    a dictionary from each list."""
    combinations = itertools.product(*dict_list_lists)  # cartesian product of all
    return map(lambda comb: functools.reduce(lambda a, b: dict(list(a.items()) + list(b.items())),
                                             comb, {}), combinations)  # return resulting dicts


def get_random_split(key, proportions):
    """Given a set of keys and the proportions to split, return the random split
    according to those keys. Returns len(proportions) boolean masks for the split"""
    unique_keys = np.unique(key)
    np.random.shuffle(unique_keys)  # in place shuffle

    # get proportional slices on the unique keys
    split_points = np.floor(np.cumsum([0] + proportions) * unique_keys.size).astype(int)
    split_slices = [slice(i, j) for i, j in zip(split_points[:-1], split_points[1:])]

    return [np.isin(key, unique_keys[split_slice]) for split_slice in split_slices]


class Metrics(Callback):
    def __init__(self, validation_data, batch_size, *args, prefix='', **kwargs):
        super().__init__(*args, **kwargs)
        self._validation_data = validation_data
        self._batch_size = batch_size
        self.prefix = prefix

    def on_epoch_end(self, epoch, logs={}):
        preds = self.model.predict_generator(
            sparse_generator(self._validation_data[0], None, self._batch_size, shuffle=False),
            steps=np.ceil(self._validation_data[0].shape[0] / self._batch_size)
        )

        predict = np.round(np.asarray(preds))
        target = self._validation_data[1]
        results = {
            'precision': precision_score(target, predict),
            'recall': recall_score(target, predict),
            'f1': f1_score(target, predict)
        }
        print(' - '.join('{}{}: {}'.format(self.prefix, name, val) for name, val in results.items()))

        for name, val in results.items():
            logs['{}{}'.format(self.prefix, name)] = val


class MyKerasClassifier(KerasSparseClassifier):
    """Custom KerasClassifier
    Ensures that we can use early stopping and checkpointing
    """
    def fit(self, X, y, **kwargs):
        # leave a 20 % chunk out on which to do validation
        val_point = int(X.shape[0] * .8)

        # try to get the checkpoint file, otherwise use a temporary
        checkpoint_file = self.sk_params.get('checkpoint_file', None)
        is_tmp = False
        if checkpoint_file is None:
            # create a temprorary file to save the checkpoint to
            is_tmp = True
            tmp_file = tempfile.NamedTemporaryFile()
            checkpoint_file = tmp_file.name

        metrics = Metrics((X[val_point:, :], y[val_point:]), 1024, prefix='val_')
        early_stopper = keras.callbacks.EarlyStopping(monitor='val_f1', min_delta=0.0001,
                                                      patience=self.sk_params.get('patience', 10),
                                                      verbose=1, mode='max')
        checkpoint = keras.callbacks.ModelCheckpoint(checkpoint_file, monitor='val_f1', verbose=1, save_best_only=True,
                                                     mode='max')

        # set the calbacks per fit method, this ensures that each clone has its own callbacks
        self.sk_params['nb_features'] = X.shape[1]
        self.sk_params['batch_size'] = 1024
        self.sk_params['callbacks'] = [metrics, checkpoint, early_stopper]

        if self.sk_params.get('class_weight', None) == 'balanced':
            weights = class_weight.compute_class_weight('balanced', [0, 1], y[val_point:])
            self.sk_params['class_weight'] = dict(enumerate(weights))

        super().fit(X[:val_point], y[:val_point], **kwargs)

        # realod from checkpoint
        self.model.load_weights(checkpoint_file)

        if is_tmp:
            # if it is temporary, delete it at the end
            tmp_file.close()