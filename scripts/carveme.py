#!/usr/bin/env python

from carveme import config, project_dir
from carveme.reconstruction.carving import carve_model, build_ensemble
from carveme.reconstruction.eggnog import load_eggnog_data
from carveme.reconstruction.gapfilling import multiGapFill
from carveme.reconstruction.media import load_media_db
from carveme.reconstruction.ncbi_download import load_ncbi_table, download_ncbi_genome
from carveme.reconstruction.scoring import reaction_scoring
from carveme.reconstruction.diamond import run_blast, load_diamond_results
from framed import load_cbmodel, Environment
import argparse
import os
import pandas as pd
from framed.io.sbml import save_cbmodel, sanitize_id
from glob import glob
from multiprocessing import Pool


def main(inputfile, input_type='protein', outputfile=None, diamond_args=None, universe=None, universe_file=None,
         ensemble_size=None, verbose=False, flavor=None, gapfill=None, init=None, mediadb=None,
         default_score=None, uptake_score=None, recursive_mode=False):

    if recursive_mode:
        model_id = os.path.splitext(os.path.basename(inputfile))[0]

        if outputfile:
            outputfile = '{}/{}.xml'.format(outputfile, model_id)
        else:
            outputfile = os.path.splitext(inputfile)[0] + '.xml'

    else:
        if outputfile:
            model_id = os.path.splitext(os.path.basename(outputfile))[0]
        else:
            model_id = os.path.splitext(os.path.basename(inputfile))[0]
            outputfile = os.path.splitext(inputfile)[0] + '.xml'

    model_id = sanitize_id(model_id)

    outputfolder = os.path.abspath(os.path.dirname(outputfile))

    if not os.path.exists(outputfolder):
        try:
            os.makedirs(outputfolder)
        except:
            print 'Unable to create output folder:', outputfolder
            return

    if input_type == 'refseq' or input_type == 'genbank':

        if verbose:
            print 'Downloading genome {} from NCBI...'.format(inputfile)

        ncbi_table = load_ncbi_table(project_dir + config.get('ncbi', input_type))
        inputfile = download_ncbi_genome(inputfile, ncbi_table)

        if not inputfile:
            print 'Failed to download genome from NCBI.'
            return

        input_type = 'protein' if inputfile.endswith('.faa.gz') else 'dna'

    if input_type == 'protein' or input_type == 'dna':
        if verbose:
            print 'Running diamond...'
        diamond_db = project_dir + config.get('input', 'diamond_db')
        blast_output = os.path.splitext(inputfile)[0] + '.tsv'
        exit_code = run_blast(inputfile, input_type, blast_output, diamond_db, diamond_args, verbose)

        if exit_code is None:
            print 'Unable to run diamond (make sure diamond is available in your PATH).'
            return

        if exit_code != 0:
            print 'Failed to run diamond.'
            if diamond_args is not None:
                print 'Incorrect diamond args? Please check documentation or use default args.'
            return

        annotations = load_diamond_results(blast_output)
    elif input_type == 'eggnog':
        annotations = load_eggnog_data(inputfile)
    else:
        raise ValueError('Invalid input type: ' + input_type)

    if verbose:
        print 'Loading universe model...'

    if not universe_file:
        if universe:
            universe_file = "{}{}universe_{}.xml".format(project_dir, config.get('generated', 'folder'), universe)
        else:
            universe_file = project_dir + config.get('generated', 'default_universe')

    try:
        universe_model = load_cbmodel(universe_file, flavor=config.get('sbml', 'default_flavor'))
        universe_model.id = model_id
    except IOError:
        if universe:
            raise IOError('Failed to load universe "{0}". Please run build_universe.py --{0}.'.format(universe))
        else:
            raise IOError('Failed to load universe model:' + universe_file)

    if gapfill or init:

        if verbose:
            print 'Loading media library...'

        if not mediadb:
            mediadb = project_dir + config.get('input', 'media_library')

        try:
            media_db = load_media_db(mediadb)
        except IOError:
            raise IOError('Failed to load media library:' + mediadb)

    if verbose:
        print 'Scoring reactions...'

    bigg_gprs = project_dir + config.get('generated', 'bigg_gprs')
    gprs = pd.read_csv(bigg_gprs)
    gprs = gprs[gprs.reaction.isin(universe_model.reactions)]

    scores = reaction_scoring(annotations, gprs)

    if scores is None:
        print 'The input genome did not match sufficient genes/reactions in the database.'
        return

    if not flavor:
        flavor = config.get('sbml', 'default_flavor')

    init_env = None

    if init:
        if init in media_db:
            init_env = Environment.from_compounds(media_db[init])
        else:
            print 'Error: medium {} not in media database.'.format(init)

    if ensemble_size is None or ensemble_size <= 1:
        if verbose:
            print 'Reconstructing a single model'
        if not gapfill:
            carve_model(universe_model, scores,
                        outputfile=outputfile,
                        flavor=flavor,
                        default_score=default_score,
                        uptake_score=uptake_score,
                        init_env=init_env)
        else:
            model = carve_model(universe_model, scores,
                                inplace=False,
                                default_score=default_score,
                                uptake_score=uptake_score,
                                init_env=init_env)
    else:
        build_ensemble(universe_model, scores, ensemble_size, outputfile, flavor, init_env=init_env)

    if gapfill:

        media = gapfill.split(',')

        if verbose:
            m1, n1 = len(model.metabolites), len(model.reactions)
            print 'Gap filling for {}...'.format(', '.join(media))

        max_uptake = config.getint('gapfill', 'max_uptake')
        scores = dict(scores[['reaction', 'normalized_score']].values)
        multiGapFill(model, universe_model, media, media_db, scores=scores, max_uptake=max_uptake, inplace=True)

        if verbose:
            m2, n2 = len(model.metabolites), len(model.reactions)
            print 'Added {} reactions and {} metabolites'.format((n2 - n1), (m2 - m1))

        save_cbmodel(model, outputfile, flavor=flavor)

    if verbose:
        print 'Done.'


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description="Reconstruct a metabolic model using CarveMe",
                                     formatter_class=argparse.RawTextHelpFormatter)

    parser.add_argument('input', metavar='INPUT',
                        help="Input (protein fasta file by default, see other options for details).\n" +
                             "When used with -r an input pattern with wildcards can also be used.\n" +
                             "When used with --refseq an NCBI RefSeq assembly accession is expected."
                        )

    input_type_args = parser.add_mutually_exclusive_group()
    input_type_args.add_argument('--dna', action='store_true', help="Build from DNA fasta file")
    input_type_args.add_argument('--egg', action='store_true', help="Build from eggNOG-mapper output file")
    input_type_args.add_argument('--refseq', action='store_true', help="Download genome from NCBI RefSeq and build")
    input_type_args.add_argument('--genbank', action='store_true', help="Download genome from NCBI GenBank and build")

    parser.add_argument('--diamond-args', help="Additional arguments for running diamond")

    parser.add_argument('-r', '--recursive', action='store_true', dest='recursive',
                        help="Bulk reconstruction from folder with genome files")

    parser.add_argument('-o', '--output', dest='output', help="SBML output file (or output folder if -r is used)")

    univ = parser.add_mutually_exclusive_group()
    univ.add_argument('-u', '--universe', dest='universe', help="Pre-built universe model (default: bacteria)")
    univ.add_argument('--universe-file', dest='universe_file', help="Reaction universe file (SBML format)")

    sbml = parser.add_mutually_exclusive_group()
    sbml.add_argument('--cobra', action='store_true', help="Output SBML in old cobra format")
    sbml.add_argument('--fbc2', action='store_true', help="Output SBML in sbml-fbc2 format")

    parser.add_argument('-n', '--ensemble', type=int, dest='ensemble',
                        help="Build model ensemble with N models")

    parser.add_argument('-g', '--gapfill', dest='gapfill',
                        help="Gap fill model for given media")

    parser.add_argument('-i', '--init', dest='init',
                        help="Initialize model with given medium")

    parser.add_argument('--mediadb', help="Media database file")

    parser.add_argument('-v', '--verbose', action='store_true', dest='verbose', help="Switch to verbose mode")

    parser.add_argument('--default-score', type=float, help=argparse.SUPPRESS)
    parser.add_argument('--uptake-score', type=float, help=argparse.SUPPRESS)

    args = parser.parse_args()

    if args.gapfill and args.ensemble:
        parser.error('Gap fill and ensemble generation cannot currently be combined (not implemented yet).')

    if args.mediadb and not args.gapfill:
        parser.error('--mediadb can only be used with --gapfill')

    if args.recursive and args.refseq:
        parser.error('-r cannot be combined with --refseq')

    if args.egg:
        input_type = 'eggnog'
    elif args.dna:
        input_type = 'dna'
    elif args.refseq:
        input_type = 'refseq'
    elif args.genbank:
        input_type = 'genbank'
    else:
        input_type = 'protein'

    if args.fbc2:
        flavor = 'fbc2'
    elif args.cobra:
        flavor = 'cobra'
    else:
        flavor = None

    if not args.recursive:

        main(inputfile=args.input,
             input_type=input_type,
             outputfile=args.output,
             diamond_args=args.diamond_args,
             universe=args.universe,
             universe_file=args.universe_file,
             ensemble_size=args.ensemble,
             verbose=args.verbose,
             flavor=flavor,
             gapfill=args.gapfill,
             init=args.init,
             mediadb=args.mediadb,
             default_score=args.default_score,
             uptake_score=args.uptake_score)

    else:
        input_files = glob(args.input)

        def f(x):
            main(inputfile=x,
                    input_type=input_type,
                    outputfile=args.output,
                    diamond_args=args.diamond_args,
                    universe=args.universe,
                    universe_file=args.universe_file,
                    ensemble_size=args.ensemble,
                    verbose=args.verbose,
                    flavor=flavor,
                    gapfill=args.gapfill,
                    init=args.init,
                    mediadb=args.mediadb,
                    default_score=args.default_score,
                    uptake_score=args.uptake_score,
                    recursive_mode=True)

        p = Pool()
        p.map(f, input_files)