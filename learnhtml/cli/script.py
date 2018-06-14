"""Command line script for web content extraction"""
import json
import os
import pickle
import pprint
import subprocess

import click
import click_log
import dask
import numpy as np
import pandas as pd
import pkg_resources
from scipy.stats._distn_infrastructure import rv_frozen

from learnhtml.features import extract_features_from_df
from learnhtml.log import logger
from learnhtml.model_selection import get_ordered_dataset, get_param_grid, nested_cv, cv_train


@click.group()
def script():
    """Feature extraction and training"""
    pass


@script.command(short_help='extract dom features')
@click_log.simple_verbosity_option(logger)
@click.argument('input_files', type=click.Path(file_okay=True, dir_okay=False, readable=True), metavar='INPUT_FILES')
@click.argument('output_files', type=click.Path(file_okay=True, dir_okay=False, writable=True), metavar='OUTPUT_FILES')
@click.option('--height', type=int, default=5, metavar='HEIGHT', help='The height of the neighbourhood')
@click.option('--depth', type=int, default=5, metavar='DEPTH', help='The depth of the neighbourhood')
@click.option('--num-workers', metavar='NUM_WORKERS', type=click.INT,
              default=8, help='The number of workers to parallelize to(default 8)')
def dom(input_files, output_files, height, depth, num_workers):
    """Extract the dom features and output them to a directory, in a partitioned fashion.

    INPUT_FILES can be a glob pattern to either a bunch of csvs containing "html,url" or
    the html files themselves. their filename will be used as url in that case("file://filename").

    OUTPUT_FILES names the pattern of the CSV files where to output the features.
    """
    dask.set_options(get=dask.multiprocessing.get, num_workers=num_workers)  # set the number of workers

    # must read as pandas because dask makes a fuss about html
    html_df = pd.read_csv(input_files)  # df of 'html'/'url'
    feats = extract_features_from_df(html_df, depth=depth, height=height, num_workers=num_workers)

    # output all the three to csvs
    logger.info('Outputting features')
    feats.to_csv(output_files, index=False)

    logger.info('DONE!')


@script.command(short_help='download and convert datasets')
@click_log.simple_verbosity_option(logger)
@click.argument('destination', metavar='DESTINATION_DIR',
                type=click.Path(dir_okay=True, file_okay=False, writable=True), nargs=1)
@click.option('-n', '--num-workers', metavar='NUM_WORKERS', type=click.INT,
              default=8, help='The number of workers to parallelize to(default 8)')
def init_datasets(destination, num_workers):
    """Download and convert Cleaneval and Dragnet datasets in DESTINATION_DIR"""
    if not os.path.exists(destination):
        logger.info('Path does not exist - creating')
        os.makedirs(destination)

    # get script location
    script_path = pkg_resources.resource_filename(__name__, 'prepare_data.sh')
    logger.info('Beginning download')  # runs subscript
    subprocess.run(['bash', script_path, destination, str(num_workers)])

    # finished
    logger.info('Done')


@script.command(short_help='train models')
@click_log.simple_verbosity_option(logger)  # add a verbosity option
@click.argument('dataset', metavar='DATASET_FILES', nargs=1)
@click.option('output', '--score-files', metavar='OUTPUT_PATTERN',
              type=click.Path(file_okay=True, dir_okay=False, writable=True),
              help='A string format for the score files. {suffix} is replaced'
                   'by "scores" and "cv" respectively.',
              default=None)
@click.option('param_file', '-j', '--param-file', metavar='PARAM_FILE',
              type=click.File(), default=None,
              help='A json file from which to read parameters')
@click.option('cli_params', '-p', '--param', type=(str, str),
              metavar='KEY VALUE',
              help='A value for a parameter given as "key value".'
                   'Values are given as json values(so quotations count).'
                   'Can be passed multiple times.',
              multiple=True)
@click.option('--external-folds', metavar='N_FOLDS TOTAL_FOLDS',
              type=click.Tuple([int, int]), default=(10, 10),
              help='The number of folds to use and the total folds '
                   'for the external loop(default 10 10). These are used for training'
                   'as well on the entire dataset.')
@click.option('--internal-folds', metavar='N_FOLDS TOTAL_FOLDS',
              type=click.Tuple([int, int]), default=(10, 10),
              help='The number of folds to use and the total folds for '
                   'the internal loop(default 10 10)')
@click.option('--n-iter', metavar='N_ITER', type=click.INT,
              default=20, help='The number of iterations for the internal '
                               'randomized search(default 20)')
@click.option('--n-jobs', metavar='N_JOBS', type=click.INT,
              default=-1, help='The number of jobs to start in parallel(default -1)')
@click.option('--random-seed', metavar='RANDOM_SEED', type=click.INT,
              default=42, help='The random seed to use')
@click.option('model_file', '--model-file', metavar='MODEL_FILE',
              type=click.Path(file_okay=True, dir_okay=False, writable=True),
              help='The file in which to save the pickled model trained'
                   'over the entire dataset.',
              default=None)
@click.option('--shuffle/--no-shuffle', default=True,
              help='Whether to shuffle the dataset beforehand')
def train(dataset, output, external_folds, internal_folds,
          n_iter, n_jobs, random_seed, param_file, model_file,
          cli_params, shuffle):
    """Trains a model over a dataset, given a set of values of parameters to use for
    the CV. Parameters used:

    """
    params = {}
    # attempt to read params from file
    if param_file is not None:
        params = json.load(param_file)

    for param in cli_params:
        # load the values from the json
        key, val = param
        loaded_val = json.loads(val)
        params[key] = loaded_val

    logger.debug('Passing params:\n{}'.format(pprint.pformat(params)))
    # extract the params
    blocks_only = params.pop('blocks_only', True)  # use only the blocks

    # load the dataset
    logger.info('Loading the dataset')
    X, y, groups = get_ordered_dataset(dataset, blocks_only=blocks_only, shuffle=shuffle)

    """Evaluate the expected f1-score with nested CV"""
    # unpacking the fold numbers
    internal_n_folds, internal_total_folds = internal_folds
    external_n_folds, external_total_folds = external_folds

    # seed the random number generator
    logger.info('Seeding the random number generator')
    np.random.seed(random_seed)
    # there is no other solution than using tf just in the worker
    # tf.set_random_seed(random_seed)

    # load the estimator
    estimator, param_distributions = get_param_grid(**params)  # get the appropriate
    logger.debug('Computed params(after default values):\n{}'.format(pprint.pformat(param_distributions)))

    # properly format params. wrap them if lists if necessary
    # rv_frozen makes an exception because it is a scipy distribution
    param_distributions = dict(
        map(lambda p: (p[0], p[1] if isinstance(p[1], list) or isinstance(p[1], rv_frozen) else [p[1]]),
            param_distributions.items()))

    # output the scores only if specified
    if output is not None:
        # training the model
        logger.info('Performing nested CV')
        scores, cv = nested_cv(estimator, X, y, groups, param_distributions=param_distributions, n_iter=n_iter,
                               internal_n_folds=internal_n_folds, internal_total_folds=internal_total_folds,
                               external_n_folds=external_n_folds, external_total_folds=external_total_folds,
                               n_jobs=n_jobs)

        # outputting
        logger.info('Saving the results')
        output_scores = output.format(suffix='scores.csv')
        output_cv = output.format(suffix='cv.csv')

        np.savetxt(output_scores, scores)
        cv.to_csv(output_cv, index=False)

    # train the model on the whole dataset only if model_file
    # is specified
    if model_file is not None:
        logger.info('Training the model over the entire dataset')
        trained_est = cv_train(estimator, X, y, groups,
                               param_distributions=param_distributions,
                               n_iter=n_iter, n_folds=external_n_folds,
                               total_folds=external_total_folds, n_jobs=n_jobs)

        logger.info('Saving the model')
        with open(model_file, 'wb') as f:
            pickle.dump(trained_est, f)  # pickle the file

    logger.info('DONE')


if __name__ == '__main__':
    script()
