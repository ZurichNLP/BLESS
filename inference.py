#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# __Author__ = 'Tannon Kew', 'Dennis Aumiller'
# __Email__ = 'kew@cl.uzh.ch', 'aumiller@informatik.uni-heidelberg.de' 
# __Date__ = '2023-03-03', '2023-03-07'

# Updates:
# refacored `inference.py` to incorporate inference with API models

import os
import sys
import time
from pathlib import Path
import random
import warnings
import logging
from typing import List, Dict, Tuple, Optional, Union

from tqdm import tqdm
from transformers import HfArgumentParser, set_seed

from utils.helpers import iter_batches, serialize_to_jsonl, get_output_file_name, persist_args
from utils.prompting import prepare_prompted_inputs, get_example_selector, postprocess_model_outputs, construct_example_template, load_predefined_prompt
from llm_inference import InferenceArguments, LLM, API_LLM

logger = logging.getLogger(__name__)



def run_inference(args):

    # set random seed everywhere for reproducibility
    set_seed(args.seed)

    # load model
    if args.model_name_or_path.lower().startswith("cohere-") or args.model_name_or_path.lower().startswith("openai-"):
        llm = API_LLM(args)
    else:
        llm = LLM(args)
    
    # initialise example selector object
    example_selector = get_example_selector(args)
    
    # construct example prompt that is reused for all inputs (with different examples)
    example_prompt = construct_example_template(args.prompt_template, args.source_field, args.target_field)
    
    with open(args.output_file, "w", encoding="utf8") if args.output_file != "stdout" else sys.stdout as outf:
        start_time = time.time()
        c = 0 # counter for generated output sequences

        for batch in tqdm(iter_batches(args.input_file, args.batch_size)):
            # input file can be a text file or a jsonl file
            # if jsonl file, we expect that the input sentence is in the key specified by args.source_field
            if isinstance(batch[0], dict):
                batch_inputs = [i[args.source_field] for i in batch]
                batch_refs = [i[args.target_field] for i in batch]
            else: # otherwise, we expect batch to be a list of strings
                batch_inputs = batch
                batch_refs = None

            # construct prompted inputs for each example in the batch
            inputs = prepare_prompted_inputs(
                inputs=batch_inputs,
                example_selector=example_selector,
                prefix=args.prompt_prefix,
                suffix=args.prompt_suffix,
                example_prompt=example_prompt,
                example_separator=args.example_separator,
                prompt_format=args.prompt_format,
            )
            
            if args.model_name_or_path.lower().startswith("cohere-") and args.trial_key:
                if c > 0 and c % 5 == 0:
                    # sleep for at least 1 minute every 5 requests to avoid hitting the rate limit
                    logger.info(f"Sleeping for 65 seconds to avoid hitting the rate limit...")
                    time.sleep(65)
                    logger.info(f"Resuming inference...")

            outputs = llm.generate_from_model(inputs)
            
            outputs = postprocess_model_outputs(inputs, outputs, args.example_separator)

            for line in serialize_to_jsonl(inputs, outputs, batch_inputs, batch_refs):
                outf.write(f"{line}\n")
                c += 1

        end_time = time.time()
        logger.info(f"Finished inference on {args.input_file} in {end_time - start_time:.4f} seconds.")
        logger.info(f"Wrote {c} outputs to {args.output_file}")
        try:
            if llm.cost > 0: # only print cost if it is available (i.e. OpenAI models)
                logger.info(f"Total tokens: {llm.total_tokens}")
                logger.info(f"Total estimated cost: ${llm.cost:.4f} USD")
        except AttributeError:
            pass
            

if __name__ == '__main__':
    parser = HfArgumentParser((InferenceArguments))
    args = parser.parse_args_into_dataclasses()[0]
    
    # Use stdout when output_file and output_dir is not specified (e.g. for debugging)
    if not args.output_file and not args.output_dir:
        args.output_file = "stdout"
    elif args.output_file:
        Path(args.output_file).parent.mkdir(parents=True, exist_ok=True)
    elif args.output_dir:
        args.output_file = get_output_file_name(args)
        Path(args.output_file).parent.mkdir(parents=True, exist_ok=True)
    else:
        raise RuntimeError(f"Could not infer output file!")
    
    # set up prompt
    args = load_predefined_prompt(args)
    # save all arguments to output file
    persist_args(args)
    
    run_inference(args)