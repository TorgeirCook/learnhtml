import os

import sparse
import numpy as np
from dask import dataframe as dd

from utils import get_random_split


def get_numpy_dataset(csv_pattern, data_cols=None, categorize_id=True, sparse_matrix=True):
    """Given a csv pattern for a dataset, return a dict of ('id', 'X', 'y') containing the components of
    the dataset.
    :param sparse_matrix: whether X should be returned as a COO matrix(greatly reduces memory use)
    :param categorize_id: if True(default), sorts the dataset by the id column, and the column is categorized
    :param data_cols: the columns to keep from the data(defaults to all)
    :param csv_pattern: the pattern of the csv files
    :return: the specified dict
    """
    ddf = dd.read_csv(csv_pattern)

    # separate the numpy arrays
    id_arr = ddf[['url', 'path']].values.compute()
    X_ddf = ddf.drop(['url', 'path', 'content_label'], axis=1)
    if data_cols is not None:
        X_ddf = ddf[data_cols]  # keep only the given cols if passed

    y_arr = ddf['content_label'].values.compute()

    # sparsify the matrix if necessary
    if sparse_matrix:
        # return the scipy COO matrix corresponding to the input
        # greatly reduces the input size
        X_arr = X_ddf.values.map_blocks(sparse.COO).astype('float32').compute().tocsr()
    else:
        X_arr = X_ddf.values.compute()

    if not categorize_id:
        return {'id': id_arr, 'X': X_arr, 'y': y_arr}

    id_arr = id_arr[:, 0]
    _, categ_id_arr = np.unique(id_arr, return_inverse=True)
    sorted_categ = np.argsort(categ_id_arr)
    return {'id': categ_id_arr[sorted_categ], 'X': X_arr[sorted_categ],
            'y': y_arr[sorted_categ]}


def get_numpy_datasets(directory):
    """
    Given a dataset directory, return the dataset as a an X and y. Also return the indices of the
    train,  validation and test samples. Keeps the relative order of the columns. :param directory: the directory of the dataset
    :return:
    """
    # get train, validation and test data

    train_data = get_numpy_dataset(os.path.join(directory, 'dom-full-train-*.csv'))
    validation_data = get_numpy_dataset(os.path.join(directory, 'dom-full-validation-*.csv'))
    test_data = get_numpy_dataset(os.path.join(directory, 'dom-full-test-*.csv'))

    # get X and y
    train_X, train_y = train_data['X'], train_data['y']
    validation_X, validation_y = validation_data['X'], validation_data['y']
    test_X, test_y = test_data['X'], test_data['y']

    # get the split indices - then build the slice objects
    split_points = np.cumsum([0, train_y.size, validation_y.size, test_y.size])
    split_slices = [slice(i, j) for i, j in zip(split_points[:-1], split_points[1:])]
    return np.concatenate((train_X, validation_X, test_X)), np.concatenate(
        (train_y, validation_y, test_y)), split_slices


def get_split_dataset(csv_pattern, data_cols=None, proportions=None):
    """Given a csv pattern, the data cols to retrieve and the proportions
    return a big concatenated X and y and the split slices."""
    if proportions is None:
        proportions = [.7, .15, .15]  # default train, validation, test, split

    # get the dataset
    dataset = get_numpy_dataset(csv_pattern, data_cols=data_cols)
    masks = get_random_split(dataset['id'][:, 0], proportions=proportions)

    # get the split points based on sizes
    split_points = np.cumsum([0] + [mask.sum() for mask in masks])
    split_slices = [slice(i, j) for i, j in zip(split_points[:-1], split_points[1:])]

    big_X = np.zeros((split_points[-1], dataset['X'].shape[1]))
    big_y = np.zeros((split_points[-1],))

    # set the portion of the array with the mask
    for split_slice, mask in zip(split_slices, masks):
        big_X[split_slice, :] = dataset['X'][mask]
        big_y[split_slice] = dataset['y'][mask]

    return big_X, big_y, split_slices
